"""Daily event clustering, following MBD: embeddings within a sliding 3-day window, edges where
cosine similarity ≥ τ, connected components of ≥ 3 articles are events, an LLM titles each event
and drops members that do not belong. Over-large or sparse components are split with Leiden."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import Counter
from datetime import date, datetime, time, timedelta

import igraph as ig
import leidenalg
import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components

from ..annotate.common import render_prompt
from ..config import TAIPEI, ingest_settings, load_yaml
from ..ingest.clean import blind, clean_title
from ..llm import client as llm
from ..llm.schemas import IN_SCOPE, EventTitle
from ..store import lake
from .embed import from_f16

log = logging.getLogger(__name__)


def _window_rows(start: datetime, end: datetime) -> list[dict]:
    scopes = ", ".join(f"'{s}'" for s in IN_SCOPE)
    days = (lake.taipei_today() - start.astimezone(TAIPEI).date()).days + 3
    return lake.rows(
        f"""
        WITH a AS (
            SELECT article_id, outlet_id, title, coalesce(published_at, fetched_at) AS published_at
            FROM {lake.source('articles', days)}
            QUALIFY row_number() OVER (PARTITION BY article_id ORDER BY created_at) = 1
        ),
        s1 AS (SELECT * FROM {lake.latest('stage1', days=days + 1, where="status = 'ok'")}),
        sa AS (SELECT * FROM {lake.latest('subtopic_assignments', days=days + 1, where="status = 'ok'")}),
        e AS (
            SELECT article_id, vector_f16 FROM {lake.source('embeddings', days + 1)}
            QUALIFY row_number() OVER (PARTITION BY article_id ORDER BY created_at DESC) = 1
        )
        SELECT a.*, s1.topic_id, coalesce(sa.subtopic_id, s1.subtopic_id) AS subtopic_id, e.vector_f16
        FROM a JOIN s1 USING (article_id) JOIN e USING (article_id) LEFT JOIN sa USING (article_id)
        WHERE s1.scope IN ({scopes}) AND a.published_at >= ? AND a.published_at < ?
        ORDER BY a.published_at
        """,
        [start, end],
    )


def cluster(X: np.ndarray, tau: float, min_size: int, max_size: int, min_density: float) -> list[list[int]]:
    """Return clusters as lists of row indices."""
    S = X @ X.T
    np.fill_diagonal(S, 0)
    adj = csr_matrix(S >= tau)
    n_comp, labels = connected_components(adj, directed=False)
    out: list[list[int]] = []
    for c in range(n_comp):
        members = np.flatnonzero(labels == c)
        if len(members) < min_size:
            continue
        sub = S[np.ix_(members, members)]
        edges = np.argwhere(np.triu(sub >= tau, k=1))
        density = len(edges) / (len(members) * (len(members) - 1) / 2)
        if len(members) <= max_size and density >= min_density:
            out.append(members.tolist())
            continue
        g = ig.Graph(n=len(members), edges=edges.tolist())
        g.es["weight"] = [float(sub[i, j]) for i, j in edges]
        part = leidenalg.find_partition(g, leidenalg.ModularityVertexPartition, weights="weight", seed=0)
        for comm in part:
            if len(comm) >= min_size:
                out.append([int(members[i]) for i in comm])
    return out


def _event_id(end: date, members: list[str]) -> str:
    return f"ev{end:%y%m%d}-" + hashlib.sha1("|".join(sorted(members)).encode()).hexdigest()[:6]


def latest_run(table: str, days: int) -> str:
    """Rows of `table` from the most recent run for each window_end (events are recomputed every publish)."""
    src = lake.source(table, days)
    return f"""(SELECT t.* FROM {src} t JOIN (SELECT window_end, max(run_id) AS run_id FROM {src} GROUP BY window_end) lr
               USING (window_end, run_id))"""


def _previous(end: date) -> dict[str, set[str]]:
    prev = end - timedelta(days=1)
    rs = lake.rows(f"SELECT event_id, article_id FROM {latest_run('event_members', 5)} WHERE window_end = ?", [prev])
    out: dict[str, set[str]] = {}
    for r in rs:
        out.setdefault(r["event_id"], set()).add(r["article_id"])
    return out


def _previous_titles(end: date) -> dict[str, dict]:
    rs = lake.rows(
        f"""SELECT event_id, title, short_title FROM {latest_run('events', 5)} WHERE window_end = ?""",
        [end - timedelta(days=1)],
    )
    return {r["event_id"]: r for r in rs}


async def _titles(groups: list[list[dict]]) -> tuple[list[EventTitle | None], list[llm.Result]]:
    cfg = llm.task_config("synth")
    system = render_prompt("event_title")
    client = llm.async_client()
    sem = asyncio.Semaphore(6)
    results: list[llm.Result] = []

    async def one(rows: list[dict]) -> EventTitle | None:
        names = ingest_settings()
        lines = []
        for i, r in enumerate(rows[:40]):
            t = blind(clean_title(r["title"]), names.outlet(r["outlet_id"]).self_names)
            lines.append(f"{i}. {t}")
        async with sem:
            res = await llm.call_async(client, llm.build_body(cfg, system, "\n".join(lines), EventTitle), EventTitle, cfg.service_tier)
        results.append(res)
        return res.parsed if res.status == "ok" else None

    out = await asyncio.gather(*(one(g) for g in groups))
    await client.close()
    return list(out), results


def _central_title(rows: list[dict], X: np.ndarray) -> str:
    S = X @ X.T
    return clean_title(rows[int(S.mean(axis=1).argmax())]["title"])


def run(window_end: date | None = None, run_id: str | None = None, use_llm: bool = True) -> dict:
    cfg = load_yaml("pipeline.yaml")["events"]
    run_id = run_id or lake.new_run_id()
    end = window_end or lake.taipei_today()
    start_d = end - timedelta(days=cfg["window_days"] - 1)
    start = datetime.combine(start_d, time.min, tzinfo=TAIPEI)
    stop = datetime.combine(end + timedelta(days=1), time.min, tzinfo=TAIPEI)
    rows = _window_rows(start, stop)
    if len(rows) < cfg["min_size"]:
        return {"articles": len(rows), "events": 0}
    X = np.stack([from_f16(r["vector_f16"]) for r in rows])
    X /= np.linalg.norm(X, axis=1, keepdims=True) + 1e-9
    clusters = cluster(X, cfg["tau"], cfg["min_size"], cfg["max_size"], cfg["min_density"])

    prev = _previous(end)
    prev_titles = _previous_titles(end)
    groups = []
    for idx in clusters:
        members = [rows[i]["article_id"] for i in idx]
        mset = set(members)
        best, best_j = None, 0.0
        for eid, pset in prev.items():
            j = len(mset & pset) / len(mset | pset)
            if j > best_j:
                best, best_j = eid, j
        eid = best if best and best_j >= cfg["continuity_jaccard"] else _event_id(end, members)
        groups.append({"idx": idx, "event_id": eid, "same_as_prev": best_j >= 0.9, "rows": [rows[i] for i in idx]})

    # Titles: reuse yesterday's when membership barely changed; otherwise ask the synthesis model.
    need = [g for g in groups if not (g["same_as_prev"] and g["event_id"] in prev_titles)]
    titles: list[EventTitle | None] = [None] * len(need)
    results: list[llm.Result] = []
    import os

    if use_llm and need and os.environ.get("OPENAI_API_KEY") and llm.spent_today() < llm.daily_budget():
        titles, results = asyncio.run(_titles([g["rows"] for g in need]))
        cfg_s = llm.task_config("synth")
        llm.record_usage("event_title", cfg_s.model, cfg_s.service_tier, results, run_id)
    for g, t in zip(need, titles):
        g["title"], g["short_title"] = (t.title, t.short_title) if t else (_central_title(g["rows"], X[g["idx"]]), "")
        if t and t.off_topic_indices:
            drop = {i for i in t.off_topic_indices if 0 <= i < len(g["rows"])}
            kept = [r for i, r in enumerate(g["rows"]) if i not in drop]
            if len(kept) >= cfg["min_size"]:
                g["rows"] = kept
    for g in groups:
        if "title" not in g:
            g["title"], g["short_title"] = prev_titles[g["event_id"]]["title"], prev_titles[g["event_id"]]["short_title"]

    ev_rows, mem_rows = [], []
    now = lake.now_utc()
    for g in groups:
        rs = g["rows"]
        topic = Counter(r["topic_id"] for r in rs).most_common(1)[0][0]
        subs = Counter(r["subtopic_id"] for r in rs if r["subtopic_id"] not in ("other", "none"))
        ev_rows.append({
            "event_id": g["event_id"], "window_end": end, "title": g["title"], "short_title": g["short_title"],
            "topic_id": topic, "subtopic_id": subs.most_common(1)[0][0] if subs else None,
            "n_articles": len(rs), "n_outlets": len({r["outlet_id"] for r in rs}),
            "first_published": min(r["published_at"] for r in rs), "last_published": max(r["published_at"] for r in rs),
            "created_at": now, "run_id": run_id,
        })
        mem_rows += [{"event_id": g["event_id"], "window_end": end, "article_id": r["article_id"], "created_at": now, "run_id": run_id} for r in rs]
    lake.write("events", ev_rows, run_id, day=end)
    lake.write("event_members", mem_rows, run_id, day=end)
    return {"articles": len(rows), "events": len(ev_rows), "clustered_articles": len(mem_rows), "titled_by_llm": sum(1 for t in titles if t)}
