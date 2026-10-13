"""Stage 1: in-scope filter + takeaways + topic/subtopic + news type (MBD's classification prompts, merged)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter

from openai.lib._parsing._responses import type_to_text_format_param

from ..cluster import taxonomy
from ..config import load_yaml
from ..llm import client as llm
from ..llm.schemas import IN_SCOPE, stage1_model
from ..store import lake
from .common import CLEAN_VERSION, article_text, render_prompt

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
CONCURRENCY = 24
FLUSH_EVERY = 50


def select_pending(limit: int, days: int = 7) -> list[dict]:
    return lake.rows(
        f"""
        WITH a AS (
            SELECT * FROM {lake.source('articles', days)}
            QUALIFY row_number() OVER (PARTITION BY article_id ORDER BY created_at) = 1
        ),
        s AS (
            SELECT article_id, bool_or(status = 'ok') AS ok, count(*) AS n
            FROM {lake.source('stage1', days + 1)} GROUP BY article_id
        )
        SELECT a.article_id, a.outlet_id, a.title, a.section, a.body, a.published_at
        FROM a LEFT JOIN s USING (article_id)
        WHERE NOT coalesce(s.ok, false) AND coalesce(s.n, 0) < {MAX_ATTEMPTS}
        ORDER BY a.published_at DESC NULLS LAST
        LIMIT {int(limit)}
        """
    )


def setup() -> tuple[llm.TaskConfig, str, type, str, taxonomy.Taxonomy]:
    cfg = llm.task_config("stage1")
    tax = taxonomy.load()
    schema = stage1_model([t["id"] for t in tax.topics], [s.id for s in tax.active_subtopics()])
    system = render_prompt("stage1", TAXONOMY=tax.prompt_text())
    phash = llm.prompt_hash(system, type_to_text_format_param(schema), CLEAN_VERSION, cfg.model, cfg.reasoning_effort)
    return cfg, system, schema, phash, tax


def to_row(article_id: str, res: llm.Result, cfg: llm.TaskConfig, phash: str, tax: taxonomy.Taxonomy, run_id: str) -> dict:
    row = {
        "article_id": article_id,
        "model": cfg.model,
        "reasoning_effort": cfg.reasoning_effort,
        "service_tier": res.service_tier,
        "prompt_hash": phash,
        "clean_version": CLEAN_VERSION,
        "status": res.status,
        "error": res.error,
        "cost_usd": llm.cost_usd(cfg.model, res.usage, res.service_tier),
        "created_at": lake.now_utc(),
        "run_id": run_id,
        **res.usage,
    }
    if res.parsed is not None:
        p = res.parsed.model_dump()
        topic, sub = p["topic_id"], p["subtopic_id"]
        if p["scope"] not in IN_SCOPE:
            topic, sub = "none", "none"
        elif sub not in ("other", "none"):
            s = next((x for x in tax.subtopics if x.id == sub), None)
            if s is None or s.topic != topic:  # subtopic must belong to the chosen topic
                sub = "other"
        row.update(
            scope=p["scope"],
            scope_reason=p["scope_reason"],
            takeaways=p["takeaways"][:5],
            topic_id=topic,
            subtopic_id=sub,
            proposed_subtopic=p["proposed_subtopic"] if sub == "other" else None,
            proposed_subtopic_desc=p["proposed_subtopic_desc"] if sub == "other" else None,
            news_type=p["news_type"],
            news_type_reason=p["news_type_reason"],
        )
    return row


async def _run(articles: list[dict], run_id: str, minutes: float) -> tuple[list[dict], list[llm.Result]]:
    """Calls run concurrently; finished rows are flushed to the lake every FLUSH_EVERY results so a
    killed job keeps its work, and no new request starts after `minutes` (the rest waits for the next run)."""
    cfg, system, schema, phash, tax = setup()
    budget_left = llm.daily_budget() - llm.spent_today()
    deadline = time.monotonic() + minutes * 60
    client = llm.async_client()
    sem = asyncio.Semaphore(CONCURRENCY)
    spent = 0.0
    rows: list[dict] = []
    pending: list[dict] = []
    results: list[llm.Result] = []

    def flush() -> None:
        if pending:
            lake.write("stage1", pending, run_id)
            pending.clear()

    async def one(a: dict) -> None:
        nonlocal spent
        async with sem:
            if spent >= budget_left or time.monotonic() > deadline:
                return
            body = llm.build_body(cfg, system, article_text(a, with_section=True), schema)
            res = await llm.call_async(client, body, schema, cfg.service_tier)
            spent += llm.cost_usd(cfg.model, res.usage, res.service_tier)
            results.append(res)
            row = to_row(a["article_id"], res, cfg, phash, tax, run_id)
            rows.append(row)
            pending.append(row)
            if len(pending) >= FLUSH_EVERY:
                flush()

    await asyncio.gather(*(one(a) for a in articles))
    await client.close()
    flush()
    if spent >= budget_left:
        log.warning("daily LLM budget reached; stage1 stopped early")
    if time.monotonic() > deadline:
        log.warning("stage1 time budget reached; the rest is left for the next run")
    return rows, results


def run(limit: int, run_id: str | None = None, minutes: float | None = None) -> dict:
    run_id = run_id or lake.new_run_id()
    articles = select_pending(limit)
    if not articles:
        return {"processed": 0}
    if llm.spent_today() >= llm.daily_budget():
        log.warning("daily LLM budget already spent; skipping stage1")
        return {"processed": 0, "budget_exhausted": True}
    minutes = minutes if minutes is not None else float(load_yaml("models.yaml")["limits"].get("stage1_minutes", 30))
    t0 = time.monotonic()
    rows, results = asyncio.run(_run(articles, run_id, minutes))
    cfg = llm.task_config("stage1")
    cost = llm.record_usage("stage1", cfg.model, cfg.service_tier, results, run_id)
    lat = sorted(r.latency for r in results if r.latency is not None)
    ok = [r for r in rows if r["status"] == "ok"]
    return {
        "processed": len(rows),
        "ok": len(ok),
        "in_scope": sum(1 for r in ok if r["scope"] in IN_SCOPE),
        "errors": len(rows) - len(ok),
        "cost_usd": round(cost, 4),
        "minutes": round((time.monotonic() - t0) / 60, 1),
        "median_latency_s": round(lat[len(lat) // 2], 1) if lat else None,
        "tiers": json.dumps(dict(Counter(r.service_tier for r in results))),
        "scopes": json.dumps({s: sum(1 for r in ok if r["scope"] == s) for s in {r["scope"] for r in ok}}, ensure_ascii=False),
    }
