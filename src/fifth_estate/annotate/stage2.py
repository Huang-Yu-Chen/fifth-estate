"""Stage 2: tone, DPP–KMT lean and China stance (article + title), reasons before scores (MBD).

Production runs through the Batch API: each scheduled run collects finished batches, then
submits the newly in-scope articles. `run_sync` (Flex) is for local testing and the bake-off.
"""

from __future__ import annotations

import asyncio
import logging

from openai.lib._parsing._responses import type_to_text_format_param

from ..llm import batch
from ..llm import client as llm
from ..llm.schemas import IN_SCOPE, Stage2
from ..store import lake
from .common import CLEAN_VERSION, article_text, render_prompt

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
TASK = "stage2"


def setup(cfg: llm.TaskConfig | None = None) -> tuple[llm.TaskConfig, str, str]:
    cfg = cfg or llm.task_config(TASK)
    system = render_prompt("stage2")
    phash = llm.prompt_hash(system, type_to_text_format_param(Stage2), CLEAN_VERSION, cfg.model, cfg.reasoning_effort)
    return cfg, system, phash


def select_pending(limit: int, days: int = 7, exclude: set[str] | None = None, phash: str | None = None) -> list[dict]:
    """In-scope articles without a successful label from the current prompt version. Never-labeled
    articles come first; articles labeled with an older codebook are re-labeled after them."""
    scopes = ", ".join(f"'{s}'" for s in IN_SCOPE)
    phash = phash or setup()[2]
    rows = lake.rows(
        f"""
        WITH s1 AS (SELECT * FROM {lake.latest('stage1', days=days + 1, where="status = 'ok'")}),
        a AS (
            SELECT * FROM {lake.source('articles', days)}
            QUALIFY row_number() OVER (PARTITION BY article_id ORDER BY created_at) = 1
        ),
        s2 AS (
            SELECT article_id,
                   bool_or(status = 'ok' AND prompt_hash = ?) AS ok_current,
                   bool_or(status = 'ok') AS ok_any,
                   count(*) FILTER (WHERE prompt_hash = ?) AS n_current
            FROM {lake.source('stage2', days + 1)} GROUP BY article_id
        )
        SELECT a.article_id, a.outlet_id, a.title, a.body, a.published_at
        FROM s1 JOIN a USING (article_id) LEFT JOIN s2 USING (article_id)
        WHERE s1.scope IN ({scopes}) AND NOT coalesce(s2.ok_current, false) AND coalesce(s2.n_current, 0) < {MAX_ATTEMPTS}
        ORDER BY coalesce(s2.ok_any, false), a.published_at DESC NULLS LAST
        """,
        [phash, phash],
    )
    exclude = exclude or set()
    return [r for r in rows if f"{TASK}:{r['article_id']}" not in exclude][:limit]


def to_row(article_id: str, res: llm.Result, model: str, effort: str | None, phash: str, run_id: str, batch_id: str | None = None) -> dict:
    row = {
        "article_id": article_id,
        "batch_id": batch_id,
        "model": model,
        "reasoning_effort": effort,
        "service_tier": res.service_tier,
        "prompt_hash": phash,
        "clean_version": CLEAN_VERSION,
        "status": res.status,
        "error": res.error,
        "cost_usd": llm.cost_usd(model, res.usage, res.service_tier),
        "created_at": lake.now_utc(),
        "run_id": run_id,
        **res.usage,
    }
    if res.parsed is not None:
        p = res.parsed.model_dump()
        if p["china_relevance"] == 0:
            p["china_stance"] = p["title_china_stance"] = None
        row.update(p)
    return row


def _estimate_cost(cfg: llm.TaskConfig, texts: list[str], system: str) -> float:
    # ~0.8 tokens per character for Traditional Chinese with o200k-style tokenizers (rough upper bound)
    tokens_in = sum(len(t) + len(system) for t in texts) * 0.8
    tokens_out = len(texts) * 1500
    return llm.cost_usd(cfg.model, {"input_tokens": int(tokens_in), "output_tokens": tokens_out}, "batch")


def collect(run_id: str) -> dict:
    client = llm.sync_client()
    lines, finished = batch.collect(client, TASK)
    if not lines:
        return {"collected": 0, "batches_finished": len(finished)}
    meta = {r["id"]: r for r in finished}
    rows, results = [], []
    for ln in lines:
        rec = meta.get(ln.batch_id, {})
        model = rec.get("model", llm.task_config(TASK).model)
        if ln.body is not None:
            res = llm.parse_response(ln.body, Stage2, "batch")
        else:
            res = llm.Result(None, "error", ln.error, {}, "batch")
        results.append(res)
        rows.append(to_row(ln.custom_id.split(":", 1)[1], res, model, rec.get("reasoning_effort"), rec.get("prompt_hash", ""), run_id, ln.batch_id))
    lake.write("stage2", rows, run_id)
    model = finished[0].get("model", llm.task_config(TASK).model) if finished else llm.task_config(TASK).model
    cost = llm.record_usage(TASK, model, "batch", results, run_id)
    return {
        "collected": len(rows),
        "ok": sum(1 for r in rows if r["status"] == "ok"),
        "batches_finished": len(finished),
        "cost_usd": round(cost, 4),
    }


def submit(limit: int, run_id: str) -> dict:
    cfg, system, phash = setup()
    pend = batch.pending_ids(TASK)
    articles = select_pending(limit, exclude=pend, phash=phash)
    if not articles:
        return {"submitted": 0, "pending_batches": len(batch.pending(TASK))}
    texts = [article_text(a) for a in articles]
    budget_left = llm.daily_budget() - llm.spent_today()
    while texts and _estimate_cost(cfg, texts, system) > budget_left:
        texts, articles = texts[: len(texts) // 2], articles[: len(articles) // 2]
    if not texts:
        log.warning("daily LLM budget reached; no stage2 batch submitted")
        return {"submitted": 0, "budget_exhausted": True}
    reqs = [(f"{TASK}:{a['article_id']}", llm.build_body(cfg, system, t, Stage2)) for a, t in zip(articles, texts)]
    bid = batch.submit(
        llm.sync_client(),
        TASK,
        reqs,
        {"model": cfg.model, "reasoning_effort": cfg.reasoning_effort, "prompt_hash": phash, "clean_version": CLEAN_VERSION},
    )
    return {"submitted": len(reqs), "batch_id": bid, "est_cost_usd": round(_estimate_cost(cfg, texts, system), 4)}


async def _run_sync(articles: list[dict], cfg: llm.TaskConfig, system: str, phash: str, run_id: str, concurrency: int = 8):
    client = llm.async_client()
    sem = asyncio.Semaphore(concurrency)
    rows, results = [], []

    async def one(a: dict) -> None:
        async with sem:
            body = llm.build_body(cfg, system, article_text(a), Stage2)
            res = await llm.call_async(client, body, Stage2, "flex")
            results.append(res)
            rows.append(to_row(a["article_id"], res, cfg.model, cfg.reasoning_effort, phash, run_id))

    await asyncio.gather(*(one(a) for a in articles))
    await client.close()
    return rows, results


def run_sync(limit: int, run_id: str | None = None, cfg: llm.TaskConfig | None = None, write: bool = True) -> tuple[dict, list[dict]]:
    run_id = run_id or lake.new_run_id()
    cfg, system, phash = setup(cfg)
    articles = select_pending(limit, exclude=batch.pending_ids(TASK), phash=phash)
    if not articles:
        return {"processed": 0}, []
    if llm.spent_today() >= llm.daily_budget():
        return {"processed": 0, "budget_exhausted": True}, []
    rows, results = asyncio.run(_run_sync(articles, cfg, system, phash, run_id))
    if write:
        lake.write("stage2", rows, run_id)
    cost = llm.record_usage(TASK, cfg.model, "flex", results, run_id)
    return {"processed": len(rows), "ok": sum(1 for r in rows if r["status"] == "ok"), "cost_usd": round(cost, 4)}, rows
