"""Per-unit, per-outlet metrics (MBD-style averages and buckets) plus the polarization index."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import mean, pstdev

from ..cluster import taxonomy
from ..cluster.events import latest_run
from ..config import load_yaml
from ..llm.schemas import IN_SCOPE, NEWS_TYPES
from ..store import lake
from .polarization import Params, attention_skew, polarization

# MBD buckets on the −5…+5 scale
BUCKETS = ((-5, -4), (-3, -2), (-1, 1), (2, 3), (4, 5))
CATEGORY_IDS = {"國內政治": "domestic", "外交": "diplomacy", "兩岸": "cross_strait"}
LEAN_BUCKET_NAMES = ("親綠", "中立偏綠", "中立", "中立偏藍", "親藍")
CHINA_BUCKET_NAMES = ("強烈抗中", "偏抗中", "中立", "偏友中", "強烈友中")
TONE_BUCKET_NAMES = ("非常負面", "負面", "中性", "正面", "非常正面")


def params() -> Params:
    return Params(**load_yaml("pipeline.yaml")["polarization"])


def analysis_rows(days: int = 95) -> list[dict]:
    """One row per in-scope article with its latest labels (Stage 2 fields are null until scored)."""
    scopes = ", ".join(f"'{s}'" for s in IN_SCOPE)
    return lake.rows(
        f"""
        WITH a AS (
            SELECT article_id, outlet_id, url, title, coalesce(published_at, fetched_at) AS published_at, via
            FROM {lake.source('articles', days)}
            QUALIFY row_number() OVER (PARTITION BY article_id ORDER BY created_at) = 1
        ),
        s1 AS (SELECT * FROM {lake.latest('stage1', days=days + 1, where="status = 'ok'")}),
        s2 AS (SELECT * FROM {lake.latest('stage2', days=days + 2, where="status = 'ok'")}),
        sa AS (SELECT * FROM {lake.latest('subtopic_assignments', days=days + 2, where="status = 'ok'")}),
        fl AS (SELECT * FROM {lake.latest('article_flags', days=days + 2)}),
        em AS (
            SELECT article_id, event_id FROM {latest_run('event_members', days + 2)}
            QUALIFY row_number() OVER (PARTITION BY article_id ORDER BY window_end DESC) = 1
        )
        SELECT a.article_id, a.outlet_id, a.url, a.title, a.published_at, a.via,
               s1.scope, s1.topic_id, coalesce(sa.subtopic_id, s1.subtopic_id) AS subtopic_id,
               s1.news_type, s1.takeaways,
               s2.tone, s2.title_tone, s2.lean, s2.title_lean, s2.china_relevance, s2.china_stance,
               s2.title_china_stance, s2.lean_reason, s2.china_reason, s2.tone_reason,
               coalesce(fl.is_wire_copy, false) AS is_wire_copy, em.event_id
        FROM a JOIN s1 USING (article_id)
        LEFT JOIN s2 USING (article_id)
        LEFT JOIN sa USING (article_id)
        LEFT JOIN fl USING (article_id)
        LEFT JOIN em USING (article_id)
        WHERE s1.scope IN ({scopes})
        """
    )


def _bucketize(scores: list[float]) -> list[int]:
    out = [0] * len(BUCKETS)
    for s in scores:
        for i, (lo, hi) in enumerate(BUCKETS):
            if lo <= s <= hi:
                out[i] += 1
                break
    return out


def _mean_ci(xs: list[float]) -> tuple[float | None, float | None]:
    if not xs:
        return None, None
    m = mean(xs)
    half = 1.96 * pstdev(xs) / math.sqrt(len(xs)) if len(xs) > 1 else None
    return round(m, 2), (round(half, 2) if half is not None else None)


@dataclass
class Unit:
    kind: str  # all | category | topic | subtopic | event
    id: str
    name: str


def outlet_stats(rows: list[dict], outlet_totals: dict[str, int]) -> dict[str, dict]:
    by = defaultdict(list)
    for r in rows:
        by[r["outlet_id"]].append(r)
    out = {}
    for o, rs in by.items():
        lean = [r["lean"] for r in rs if r["lean"] is not None]
        title_lean = [r["title_lean"] for r in rs if r["title_lean"] is not None]
        china = [r["china_stance"] for r in rs if r["china_stance"] is not None]
        tone = [r["tone"] for r in rs if r["tone"] is not None]
        lm, lci = _mean_ci(lean)
        cm, cci = _mean_ci(china)
        tm, tci = _mean_ci(tone)
        types = Counter(r["news_type"] for r in rs)
        out[o] = {
            "n": len(rs),
            "share": round(len(rs) / outlet_totals[o], 4) if outlet_totals.get(o) else None,
            "n_scored": len(lean),
            "lean": lm,
            "lean_ci": lci,
            "lean_buckets": _bucketize(lean),
            "title_lean": _mean_ci(title_lean)[0],
            "china_n": len(china),
            "china": cm,
            "china_ci": cci,
            "china_buckets": _bucketize(china),
            "tone": tm,
            "tone_buckets": _bucketize(tone),
            "news_types": [types.get(t, 0) for t in NEWS_TYPES],
            "wire_share": round(sum(1 for r in rs if r["is_wire_copy"]) / len(rs), 3),
        }
    return out


def unit_metrics(rows: list[dict], outlet_totals: dict[str, int], baseline: dict[str, float], p: Params, seed: int = 0) -> dict:
    scored = [r for r in rows if r["lean"] is not None]
    lean_by = defaultdict(list)
    china_by = defaultdict(list)
    for r in scored:
        lean_by[r["outlet_id"]].append(r["lean"])
        if r["china_relevance"] and r["china_stance"] is not None:
            china_by[r["outlet_id"]].append(r["china_stance"])
    stats = outlet_stats(rows, outlet_totals)
    attention = {o: s["share"] or 0.0 for o, s in stats.items()}
    for o in outlet_totals:
        attention.setdefault(o, 0.0)
    return {
        "n": len(rows),
        "n_scored": len(scored),
        "polarization": polarization(lean_by, china_by, n_total=len(scored), params=p, seed=seed),
        "attention_skew": attention_skew(attention, baseline),
        "outlets": stats,
    }


def window_rows(rows: list[dict], days: int, now: datetime) -> list[dict]:
    since = now - timedelta(days=days)
    return [r for r in rows if r["published_at"] and r["published_at"] >= since]


def baselines(rows: list[dict]) -> tuple[dict[str, float], dict[str, float]]:
    lean, china = defaultdict(list), defaultdict(list)
    for r in rows:
        if r["lean"] is not None:
            lean[r["outlet_id"]].append(r["lean"])
        if r["china_stance"] is not None:
            china[r["outlet_id"]].append(r["china_stance"])
    return ({o: round(mean(v), 2) for o, v in lean.items()}, {o: round(mean(v), 2) for o, v in china.items()})


def units_for(rows: list[dict], tax: taxonomy.Taxonomy, events: dict[str, dict]) -> dict[tuple[str, str], tuple[Unit, list[dict]]]:
    """Group rows into analysis units."""
    out: dict[tuple[str, str], tuple[Unit, list[dict]]] = {}

    def add(kind: str, uid: str, name: str, r: dict) -> None:
        key = (kind, uid)
        if key not in out:
            out[key] = (Unit(kind, uid, name), [])
        out[key][1].append(r)

    for r in rows:
        add("all", "all", "所有政治新聞", r)
        add("category", CATEGORY_IDS[r["scope"]], r["scope"], r)
        t = tax.topic(r["topic_id"])
        if t:
            add("topic", t["id"], t["name"], r)
        sid = tax.resolve(r["subtopic_id"])
        sub = next((s for s in tax.subtopics if s.id == sid), None)
        if sub:
            add("subtopic", sub.id, sub.name, r)
        if r["event_id"] and r["event_id"] in events:
            add("event", r["event_id"], events[r["event_id"]]["title"], r)
    return out


def trend(rows: list[dict], outlet_totals_fn, baseline: dict[str, float], p: Params, now: datetime, days: int = 30, span: int = 7) -> list[dict]:
    """Daily points of a trailing `span`-day index (no bootstrap)."""
    quick = Params(**{**p.__dict__, "bootstrap": 0})
    out = []
    for d in range(days - 1, -1, -1):
        end = (now - timedelta(days=d)).replace(hour=23, minute=59, second=59)
        start = end - timedelta(days=span)
        rs = [r for r in rows if r["published_at"] and start < r["published_at"] <= end]
        if not rs:
            out.append({"date": end.date().isoformat(), "n": 0, "P": None})
            continue
        m = unit_metrics(rs, outlet_totals_fn(start, end), baseline, quick)
        pol = m["polarization"]
        out.append({"date": end.date().isoformat(), "n": len(rs), "P": pol["P"] if pol["sufficient"] else None,
                    "P_lean": pol["P_lean"], "P_china": pol["P_china"]})
    return out


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
