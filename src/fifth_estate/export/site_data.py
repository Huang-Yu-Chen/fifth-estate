"""Write the static JSON the dashboard reads (web/public/data) and the small public snapshot (public-data/)."""

from __future__ import annotations

import json
import logging
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean

from ..cluster import taxonomy
from ..cluster.events import latest_run
from ..config import REPO_ROOT, TAIPEI, ingest_settings, load_yaml
from ..llm.schemas import NEWS_TYPES
from ..metrics import aggregate as agg
from ..store import lake

log = logging.getLogger(__name__)
SITE_DATA = REPO_ROOT / "web" / "public" / "data"
PUBLIC_DATA = REPO_ROOT / "public-data"
MAX_ARTICLES = 300


def _dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str), encoding="utf-8")


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(TAIPEI).isoformat(timespec="minutes") if dt else None


def article_dict(r: dict) -> dict:
    return {
        "id": r["article_id"],
        "outlet": r["outlet_id"],
        "title": r["title"],
        "url": r["url"],
        "published": _iso(r["published_at"]),
        "type": r["news_type"],
        "tone": r["tone"],
        "lean": r["lean"],
        "title_lean": r["title_lean"],
        "china": r["china_stance"],
        "china_rel": r["china_relevance"],
        "lean_reason": (r["lean_reason"] or "")[:160] or None,
        "china_reason": (r["china_reason"] or "")[:160] or None if r["china_relevance"] else None,
        "takeaways": list(r["takeaways"] or [])[:4],
        "topic": r["topic_id"],
        "subtopic": r["subtopic_id"],
        "event": r["event_id"],
        "wire": bool(r["is_wire_copy"]),
        "via": r["via"],
    }


def load_events(days: int = 16) -> dict[str, dict]:
    rs = lake.rows(
        f"""SELECT * FROM {latest_run('events', days)}
            QUALIFY row_number() OVER (PARTITION BY event_id ORDER BY window_end DESC) = 1"""
    )
    return {r["event_id"]: r for r in rs}


def export(out_dir: Path = SITE_DATA, public_dir: Path | None = PUBLIC_DATA) -> dict:
    now = agg.now_utc()
    cfg = load_yaml("pipeline.yaml")
    windows: list[int] = cfg["windows"]
    p = agg.params()
    tax = taxonomy.load()
    settings = ingest_settings()
    rows = agg.analysis_rows(days=max(windows) + 5)
    events = load_events()
    base_lean, base_china = agg.baselines(agg.window_rows(rows, cfg["baseline_days"], now))

    def totals(rs: list[dict]) -> dict[str, int]:
        return dict(Counter(r["outlet_id"] for r in rs))

    win_rows = {w: agg.window_rows(rows, w, now) for w in windows}
    win_totals = {w: totals(win_rows[w]) for w in windows}

    def totals_between(start: datetime, end: datetime) -> dict[str, int]:
        return totals([r for r in rows if r["published_at"] and start < r["published_at"] <= end])

    if out_dir.exists():
        shutil.rmtree(out_dir)
    units = agg.units_for(rows, tax, events)
    index: dict[str, list[dict]] = defaultdict(list)  # kind -> summaries
    grid: dict[str, dict] = {str(w): defaultdict(dict) for w in windows}

    for i, ((kind, uid), (unit, urows)) in enumerate(sorted(units.items())):
        doc: dict = {"kind": kind, "id": uid, "name": unit.name, "windows": {}}
        if kind == "topic":
            t = tax.topic(uid)
            doc.update(category=tax.categories[t["category"]], description=t["description"])
        elif kind == "subtopic":
            s = next(x for x in tax.subtopics if x.id == uid)
            t = tax.topic(s.topic)
            doc.update(topic=s.topic, topic_name=t["name"] if t else None, description=s.desc, origin=s.origin)
        elif kind == "event":
            e = events[uid]
            doc.update(short_title=e["short_title"], topic=e["topic_id"], subtopic=e["subtopic_id"],
                       first=_iso(e["first_published"]), last=_iso(e["last_published"]), window_end=str(e["window_end"]))

        if kind == "event":
            m = agg.unit_metrics(urows, totals(urows) | {}, base_lean, p, seed=i)
            m["outlets"] = {o: {**s, "share": None} for o, s in m["outlets"].items()}
            doc["windows"]["event"] = m
            summary_metrics = m
        else:
            for w in windows:
                wr = agg.window_rows(urows, w, now)
                if not wr:
                    continue
                m = agg.unit_metrics(wr, win_totals[w], base_lean, p, seed=i)
                no_op = [r for r in wr if r["news_type"] != "評論"]
                m["no_opinion"] = agg.unit_metrics(no_op, win_totals[w], base_lean, p, seed=i)["polarization"] if no_op else None
                doc["windows"][str(w)] = m
                if kind == "topic":
                    for o, s in m["outlets"].items():
                        grid[str(w)][o][uid] = {"n": s["n"], "lean": s["lean"], "china": s["china"], "tone": s["tone"]}
            doc["trend"] = agg.trend(urows, totals_between, base_lean, p, now) if kind in ("all", "category", "topic", "subtopic") else []
            summary_metrics = doc["windows"].get(str(windows[0])) or doc["windows"].get(str(windows[-1]))

        arts = sorted(urows, key=lambda r: r["published_at"] or now, reverse=True)[:MAX_ARTICLES]
        doc["articles"] = [article_dict(r) for r in arts]
        _dump(out_dir / "units" / kind / f"{uid}.json", doc)

        index[kind].append(
            {
                "id": uid,
                "name": unit.name,
                **{k: doc[k] for k in ("category", "topic", "topic_name", "short_title", "first", "last", "origin") if k in doc},
                "windows": {
                    wk: {"n": wm["n"], "n_scored": wm["n_scored"], **{k: wm["polarization"][k] for k in ("P", "P_lean", "P_china", "ci_low", "ci_high", "sufficient", "n_outlets", "china_axis_used")},
                         "attention_skew": wm["attention_skew"],
                         "outlets": {o: s["n"] for o, s in wm["outlets"].items()}}
                    for wk, wm in doc["windows"].items()
                },
            }
        )

    # outlets: baseline + weekly series + topic mix + recent articles
    outlet_meta = []
    for o in settings.outlets:
        ors = [r for r in rows if r["outlet_id"] == o.id]
        weekly = []
        for k in range(12, -1, -1):
            end = now - timedelta(days=7 * k)
            wk = [r for r in ors if r["published_at"] and end - timedelta(days=7) < r["published_at"] <= end]
            lean = [r["lean"] for r in wk if r["lean"] is not None]
            china = [r["china_stance"] for r in wk if r["china_stance"] is not None]
            weekly.append({"week_end": end.date().isoformat(), "n": len(wk),
                           "lean": round(mean(lean), 2) if lean else None, "china": round(mean(china), 2) if china else None})
        mix = Counter(r["topic_id"] for r in agg.window_rows(ors, 30, now))
        _dump(out_dir / "outlets" / f"{o.id}.json", {
            "id": o.id, "name": o.name, "short": o.short, "via": o.via,
            "baseline_lean": base_lean.get(o.id), "baseline_china": base_china.get(o.id),
            "weekly": weekly, "topic_mix_30d": dict(mix.most_common()),
            "articles": [article_dict(r) for r in sorted(ors, key=lambda r: r["published_at"] or now, reverse=True)[:100]],
        })
        outlet_meta.append({"id": o.id, "name": o.name, "short": o.short, "via": o.via,
                            "baseline_lean": base_lean.get(o.id), "baseline_china": base_china.get(o.id),
                            "n": {str(w): win_totals[w].get(o.id, 0) for w in windows}})
    # MBD orders publishers by data-driven lean
    outlet_meta.sort(key=lambda x: (x["baseline_lean"] is None, x["baseline_lean"] if x["baseline_lean"] is not None else 0))

    models = load_yaml("models.yaml")["tasks"]
    fake = lake.rows(f"SELECT count(*) AS n FROM {lake.source('stage2', max(windows) + 5)} WHERE model = 'FAKE'")[0]["n"]
    meta = {
        "generated_at": _iso(now),
        "fake_data": bool(fake),
        "site": load_yaml("pipeline.yaml").get("site", {}),
        "windows": windows,
        "outlets": outlet_meta,
        "categories": tax.categories,
        "topics": [{"id": t["id"], "name": t["name"], "category": t["category"], "description": t["description"]} for t in tax.topics],
        "subtopics": [{"id": s.id, "topic": s.topic, "name": s.name, "desc": s.desc, "origin": s.origin} for s in tax.active_subtopics()],
        "news_types": list(NEWS_TYPES),
        "buckets": {"lean": agg.LEAN_BUCKET_NAMES, "china": agg.CHINA_BUCKET_NAMES, "tone": agg.TONE_BUCKET_NAMES},
        "models": {k: {"model": v["model"], "reasoning_effort": v.get("reasoning_effort")} for k, v in models.items()},
        "params": {"polarization": load_yaml("pipeline.yaml")["polarization"], "events": load_yaml("pipeline.yaml")["events"]},
        "counts": {"articles_in_scope": len(rows), "scored": sum(1 for r in rows if r["lean"] is not None),
                   **{f"window_{w}": len(win_rows[w]) for w in windows}},
    }
    _dump(out_dir / "meta.json", meta)
    _dump(out_dir / "index.json", {k: v for k, v in index.items()})
    _dump(out_dir / "grid.json", {w: {o: dict(c) for o, c in g.items()} for w, g in grid.items()})
    recent_events = sorted(index.get("event", []), key=lambda e: e.get("last") or "", reverse=True)
    _dump(out_dir / "events.json", recent_events[:200])

    if public_dir is not None:
        day = now.astimezone(TAIPEI).date().isoformat()
        snap = {
            "date": day,
            "window_days": windows[0],
            "units": [
                {"kind": k, "id": u["id"], "name": u["name"], **{kk: vv for kk, vv in u["windows"].get(str(windows[0]), {}).items()}}
                for k in ("all", "category", "topic", "subtopic")
                for u in index.get(k, [])
                if str(windows[0]) in u["windows"]
            ],
            "outlets": outlet_meta,
        }
        _dump(public_dir / f"{day}.json", snap)
    return {"units": sum(len(v) for v in index.values()), "articles": len(rows), "scored": meta["counts"]["scored"], "events": len(index.get("event", []))}
