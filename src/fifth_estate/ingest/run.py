"""Ingest orchestration: discover -> filter -> fetch -> extract -> store."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta

from ..config import Outlet, ingest_settings
from ..store import lake, state
from . import dates
from .extract import extract
from .fetch import Fetcher
from .sources import Candidate, discover
from .urls import article_id, canonicalize, matches_any

log = logging.getLogger(__name__)

FINAL_STATUSES = ("ok", "skip_section", "robots", "too_old", "no_date", "not_provider", "too_short", "gone")
MIN_BODY_CHARS = 80


@dataclass
class OutletReport:
    outlet_id: str
    discovered: int = 0
    new: int = 0
    statuses: Counter = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)


def _seen(days: int = 7) -> dict[str, tuple[bool, int]]:
    """canonical_url -> (final?, attempts)."""
    final_list = ", ".join(f"'{s}'" for s in FINAL_STATUSES)
    rs = lake.rows(
        f"""SELECT canonical_url, bool_or(status IN ({final_list})) AS final, count(*) AS attempts
            FROM {lake.source('fetches', days)} GROUP BY canonical_url"""
    )
    return {r["canonical_url"]: (bool(r["final"]), int(r["attempts"])) for r in rs}


def _section_allowed(outlet: Outlet, section: str | None) -> bool | None:
    """True/False if the outlet has section rules and the section is known; None if undecidable."""
    if not outlet.allow_sections and not outlet.deny_sections:
        return None
    if not section:
        return None
    if outlet.deny_sections and matches_any(outlet.deny_sections, section):
        return False
    if outlet.allow_sections:
        return matches_any(outlet.allow_sections, section)
    return True


async def ingest_outlet(
    fetcher: Fetcher,
    outlet: Outlet,
    run_id: str,
    seen: dict[str, tuple[bool, int]],
    section_keys: dict[str, dict[str, str]],
    since_hours: float,
    max_fetch: int,
) -> tuple[OutletReport, list[dict], list[dict]]:
    settings = fetcher.settings
    rep = OutletReport(outlet.id)
    fetch_rows: list[dict] = []
    article_rows: list[dict] = []
    now = lake.now_utc()
    cutoff = now - timedelta(hours=since_hours)

    cands, errors = await discover(fetcher, outlet)
    rep.errors.extend(errors)
    rep.discovered = len(cands)

    # Merge duplicates (same article from RSS + sitemap + listing), keeping the richest hints.
    merged: dict[str, Candidate] = {}
    for c in cands:
        cu = canonicalize(c.url)
        if cu in merged:
            m = merged[cu]
            m.title_hint = m.title_hint or c.title_hint
            m.published_hint = m.published_hint or c.published_hint
            m.keywords = m.keywords or c.keywords
        else:
            merged[cu] = c

    keys = section_keys.setdefault(outlet.id, {})
    todo: list[tuple[str, Candidate, str | None]] = []
    for cu, c in merged.items():
        if outlet.url_include and not matches_any(outlet.url_include, cu):
            continue
        if outlet.url_exclude and matches_any(outlet.url_exclude, cu):
            continue
        if c.published_hint and c.published_hint < cutoff:
            continue
        final, attempts = seen.get(cu, (False, 0))
        if final or attempts >= settings.max_fetch_attempts:
            continue
        key = None
        if outlet.section_key:
            m = re.search(outlet.section_key, cu)
            key = m.group(1) if m else None
            if key and keys.get(key) == "deny":
                continue
        todo.append((cu, c, key))

    # Newest first. For outlets with section codes in the URL, probe one article per never-seen code
    # first; the rest of that code's articles wait until the probe has decided allow/deny.
    todo.sort(key=lambda t: -(t[1].published_hint.timestamp() if t[1].published_hint else now.timestamp()))
    probes, known, waiting, probe_keys = [], [], [], set()
    for t in todo:
        key = t[2]
        if key and key not in keys:
            (waiting if key in probe_keys else probes).append(t)
            probe_keys.add(key)
        else:
            known.append(t)
    todo = probes + known + waiting
    rep.new = len(todo)
    fetched = 0

    for cu, c, key in todo:
        if fetched >= max_fetch:
            break
        if key and keys.get(key) == "deny":
            continue  # decided by a probe earlier in this run; costs nothing
        fetched += 1
        row = {
            "run_id": run_id,
            "outlet_id": outlet.id,
            "url": c.url,
            "canonical_url": cu,
            "source": c.source,
            "title_hint": c.title_hint,
            "published_hint": c.published_hint,
            "attempt": seen.get(cu, (False, 0))[1] + 1,
            "created_at": lake.now_utc(),
        }
        try:
            if not await fetcher.allowed(c.url):
                row["status"] = "robots"
            else:
                r = await fetcher.get(c.url, attempts=2)
                row["http_status"] = r.status_code
                if r.status_code in (404, 410):
                    row["status"] = "gone"
                elif r.status_code >= 400:
                    row["status"] = "http_error"
                else:
                    ex = extract(r.text, str(r.url), outlet)
                    row["section"] = ex.section
                    allowed = _section_allowed(outlet, ex.section)
                    if key and allowed is not None and key not in keys:
                        keys[key] = "allow" if allowed else "deny"
                    published = ex.published_at or c.published_hint
                    if not dates.plausible(published):
                        published = c.published_hint if dates.plausible(c.published_hint) else None
                    if outlet.via == "yahoo" and ex.provider_id and ex.provider_id != _provider(outlet):
                        row["status"] = "not_provider"
                    elif allowed is False:
                        row["status"] = "skip_section"
                    elif published and published < cutoff - timedelta(hours=24):
                        row["status"] = "too_old"
                    elif published is None:
                        row["status"] = "no_date"
                    elif len(ex.body) < MIN_BODY_CHARS:
                        row["status"] = "too_short" if ex.body else "extract_failed"
                    else:
                        row["status"] = "ok"
                        final_url = canonicalize(ex.canonical_url) if ex.canonical_url and outlet.via != "yahoo" else cu
                        article_rows.append(
                            {
                                "article_id": article_id(outlet.id, cu),
                                "outlet_id": outlet.id,
                                "url": final_url,
                                "canonical_url": cu,
                                "source": c.source,
                                "via": outlet.via,
                                "provider_id": ex.provider_id,
                                "discovered_at": now,
                                "fetched_at": lake.now_utc(),
                                "published_at": published,
                                "title": ex.title or c.title_hint,
                                "section": ex.section,
                                "body": ex.body,
                                "n_chars": len(ex.body),
                                "method": ex.method,
                                "byline": ex.byline,
                                "keywords": c.keywords,
                                "content_hash": hashlib.sha1(re.sub(r"\s+", "", ex.body).encode()).hexdigest()[:16],
                                "run_id": run_id,
                                "created_at": lake.now_utc(),
                            }
                        )
        except Exception as e:
            row["status"] = "http_error"
            row["error"] = f"{type(e).__name__}: {e}"[:300]
        rep.statuses[row["status"]] += 1
        fetch_rows.append(row)
    return rep, fetch_rows, article_rows


def _provider(outlet: Outlet) -> str | None:
    return next((s.provider for s in outlet.sources if s.type == "yahoo_provider"), None)


async def ingest(outlet_ids: list[str] | None = None, since_hours: float = 36, max_fetch: int = 150) -> list[OutletReport]:
    settings = ingest_settings()
    outlets = [o for o in settings.outlets if not outlet_ids or o.id in outlet_ids]
    run_id = lake.new_run_id()
    seen = _seen()
    section_keys = state.load("section_keys.json")
    async with Fetcher(settings) as fetcher:
        results = await asyncio.gather(
            *(ingest_outlet(fetcher, o, run_id, seen, section_keys, since_hours, max_fetch) for o in outlets)
        )
    fetch_rows = [r for _, f, _ in results for r in f]
    article_rows = [r for _, _, a in results for r in a]
    lake.write("fetches", fetch_rows, run_id)
    lake.write("articles", article_rows, run_id)
    state.save("section_keys.json", section_keys)
    return [rep for rep, _, _ in results]
