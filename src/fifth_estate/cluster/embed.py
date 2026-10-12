"""Embeddings for event clustering: title + Stage 1 takeaways (outlet-neutral wording)."""

from __future__ import annotations

import hashlib
import logging

import numpy as np

from ..config import ingest_settings
from ..ingest.clean import blind, clean_title
from ..llm import client as llm
from ..llm.schemas import IN_SCOPE
from ..store import lake

log = logging.getLogger(__name__)
CHUNK = 100


def embed_text(row: dict) -> str:
    names = ingest_settings().outlet(row["outlet_id"]).self_names
    title = blind(clean_title(row["title"]), names)
    return title + "\n" + "\n".join(row["takeaways"] or [])


def select_pending(limit: int, days: int = 7) -> list[dict]:
    scopes = ", ".join(f"'{s}'" for s in IN_SCOPE)
    return lake.rows(
        f"""
        WITH s1 AS (SELECT * FROM {lake.latest('stage1', days=days + 1, where="status = 'ok'")}),
        a AS (
            SELECT article_id, outlet_id, title, published_at FROM {lake.source('articles', days)}
            QUALIFY row_number() OVER (PARTITION BY article_id ORDER BY created_at) = 1
        ),
        e AS (SELECT DISTINCT article_id FROM {lake.source('embeddings', days + 1)})
        SELECT a.article_id, a.outlet_id, a.title, s1.takeaways
        FROM s1 JOIN a USING (article_id) LEFT JOIN e USING (article_id)
        WHERE s1.scope IN ({scopes}) AND e.article_id IS NULL
        ORDER BY a.published_at DESC NULLS LAST
        LIMIT {int(limit)}
        """
    )


def to_f16(vec: list[float]) -> bytes:
    return np.asarray(vec, dtype=np.float16).tobytes()


def from_f16(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float16).astype(np.float32)


def run(limit: int, run_id: str | None = None) -> dict:
    run_id = run_id or lake.new_run_id()
    cfg = llm.task_config("embed")
    rows = select_pending(limit)
    if not rows:
        return {"embedded": 0}
    if llm.spent_today() >= llm.daily_budget():
        return {"embedded": 0, "budget_exhausted": True}
    client = llm.sync_client()
    out, tokens = [], 0
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i : i + CHUNK]
        texts = [embed_text(r) for r in chunk]
        resp = client.embeddings.create(model=cfg.model, input=texts, dimensions=cfg.dimensions)
        tokens += resp.usage.total_tokens
        for r, t, d in zip(chunk, texts, resp.data):
            out.append(
                {
                    "article_id": r["article_id"],
                    "model": cfg.model,
                    "dims": cfg.dimensions,
                    "text_hash": hashlib.sha1(t.encode()).hexdigest()[:12],
                    "vector_f16": to_f16(d.embedding),
                    "input_tokens": resp.usage.total_tokens // max(len(chunk), 1),
                    "cost_usd": llm.cost_usd(cfg.model, {"input_tokens": resp.usage.total_tokens // max(len(chunk), 1)}, "default"),
                    "created_at": lake.now_utc(),
                    "run_id": run_id,
                }
            )
    lake.write("embeddings", out, run_id)
    cost = llm.cost_usd(cfg.model, {"input_tokens": tokens}, "default")
    lake.write(
        "llm_usage",
        [{"task": "embed", "model": cfg.model, "service_tier": "default", "n_requests": len(out), "input_tokens": tokens,
          "cached_tokens": 0, "output_tokens": 0, "cost_usd": cost, "created_at": lake.now_utc(), "run_id": run_id}],
        run_id,
    )
    return {"embedded": len(out), "cost_usd": round(cost, 4)}
