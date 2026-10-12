"""Stratified samples of articles for human labeling (gold set, weekly MBD-style checks)."""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path

from ..config import data_dir
from ..llm.schemas import IN_SCOPE
from ..store import lake

SETS = "validation/sets"


def set_path(name: str) -> Path:
    p = data_dir() / SETS / f"{name}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _bucket(x: int | None) -> int | None:
    if x is None:
        return None
    return 0 if x <= -4 else 1 if x <= -2 else 2 if x <= 1 else 3 if x <= 3 else 4


def candidates(days: int) -> list[dict]:
    scopes = ", ".join(f"'{s}'" for s in IN_SCOPE)
    return lake.rows(
        f"""
        WITH s1 AS (SELECT * FROM {lake.latest('stage1', days=days + 1, where="status = 'ok'")}),
        s2 AS (SELECT * FROM {lake.latest('stage2', days=days + 2, where="status = 'ok'")}),
        a AS (
            SELECT article_id, outlet_id, n_chars FROM {lake.source('articles', days)}
            QUALIFY row_number() OVER (PARTITION BY article_id ORDER BY created_at) = 1
        )
        SELECT a.article_id, a.outlet_id, s1.scope, s2.lean
        FROM a JOIN s1 USING (article_id) LEFT JOIN s2 USING (article_id)
        WHERE s1.scope IN ({scopes}) AND a.n_chars >= 200
        """
    )


def gold(n: int = 150, days: int = 14, seed: int = 42) -> list[str]:
    """Spread across outlets × category × predicted lean bucket, so the extremes are represented."""
    rng = random.Random(seed)
    cells: dict[tuple, list[str]] = defaultdict(list)
    for r in candidates(days):
        cells[(r["outlet_id"], r["scope"], _bucket(r["lean"]))].append(r["article_id"])
    for v in cells.values():
        rng.shuffle(v)
    picked: list[str] = []
    while len(picked) < n and any(cells.values()):
        for key in rng.sample(list(cells), len(cells)):
            if cells[key] and len(picked) < n:
                picked.append(cells[key].pop())
    return picked


def weekly(days: int = 7, per_bucket: int = 2, seed: int | None = None) -> list[str]:
    """MBD: two articles per lean bucket, and at least one per outlet."""
    rng = random.Random(seed)
    rows = [r for r in candidates(days) if r["lean"] is not None]
    rng.shuffle(rows)
    picked: dict[str, dict] = {}
    for b in range(5):
        for r in [r for r in rows if _bucket(r["lean"]) == b][:per_bucket]:
            picked[r["article_id"]] = r
    for o in {r["outlet_id"] for r in rows}:
        if not any(p["outlet_id"] == o for p in picked.values()):
            r = next(r for r in rows if r["outlet_id"] == o)
            picked[r["article_id"]] = r
    return list(picked)


def save(name: str, ids: list[str]) -> Path:
    p = set_path(name)
    p.write_text(json.dumps({"name": name, "created": lake.taipei_today().isoformat(), "article_ids": ids}, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def load(name: str) -> list[str]:
    return json.loads(set_path(name).read_text(encoding="utf-8"))["article_ids"]
