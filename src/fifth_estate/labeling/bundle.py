"""A label set's blinded bundle: everything the labeling site serves. Built on the laptop from the
lake, so the site itself never sees the lake.

Each article gets a random key. The bundle maps keys back to article ids (for `sync`), but the site
only ever sends a browser the key, the blinded title and body, and the codebook.
"""

from __future__ import annotations

import json
import secrets

from ..annotate.common import CLEAN_VERSION, blinded
from ..store import lake
from ..validate import sample
from . import codebook


def _articles(ids: list[str]) -> dict[str, dict]:
    rows = lake.rows(
        f"""SELECT article_id, outlet_id, title, body FROM {lake.source('articles')}
            WHERE list_contains(?, article_id)
            QUALIFY row_number() OVER (PARTITION BY article_id ORDER BY created_at) = 1""",
        [ids],
    )
    return {r["article_id"]: r for r in rows}


def _new_key(used: set[str]) -> str:
    key = secrets.token_hex(6)
    while key in used:
        key = secrets.token_hex(6)
    used.add(key)
    return key


def build(set_name: str, coders_per_article: int, previous: dict | None = None, title: str | None = None) -> tuple[dict, list[str]]:
    """The bundle for validation/sets/{set_name}.json, and the set's article ids missing from the lake.
    Keys already in `previous` are kept, so labels made before a rebuild stay attached."""
    prev = previous or {}
    ids = sample.load(set_name)
    arts = _articles(ids)
    keys = {it["article_id"]: it["key"] for it in prev.get("items", [])}
    used = set(keys.values())
    items, missing = [], []
    for aid in ids:
        if aid not in arts:
            missing.append(aid)
            continue
        if aid not in keys:
            keys[aid] = _new_key(used)
        head, body = blinded(arts[aid])
        items.append({"key": keys[aid], "article_id": aid, "title": head, "body": body})
    secs = codebook.sections()
    now = lake.now_utc().isoformat(timespec="seconds")
    return {
        "set": set_name,
        "title": title or prev.get("title") or set_name,
        "active": True,
        "coders_per_article": coders_per_article,
        "created_at": prev.get("created_at", now),
        "updated_at": now,
        "clean_version": CLEAN_VERSION,
        "codebook_version": codebook.version(secs),
        "codebook_note": codebook.NOTE,
        "codebook": secs,
        "items": items,
    }, missing


def mark_set(set_name: str, coders_per_article: int) -> None:
    """Record the design in validation/sets/{set}.json (the monitoring dashboard reads it there)."""
    p = sample.set_path(set_name)
    d = json.loads(p.read_text(encoding="utf-8"))
    d["coders_per_article"] = coders_per_article
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
