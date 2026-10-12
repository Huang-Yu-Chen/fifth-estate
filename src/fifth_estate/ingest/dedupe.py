"""Wire-copy flags: articles that republish 中央社 (CNA) copy. Counted by default (an outlet's
choice to run wire copy is still its selection); the dashboard can exclude them."""

from __future__ import annotations

import re

from datasketch import MinHash, MinHashLSH

from ..config import ingest_settings, load_yaml
from ..store import lake
from .clean import clean_body

SHINGLE = 5
PERM = 64
_MARKER = re.compile(r"[（(]中央社")
_TAIL = re.compile(r"[（(]中央社[）)]\s*$")


def _minhash(text: str) -> MinHash:
    t = re.sub(r"\s+", "", text)
    m = MinHash(num_perm=PERM)
    for i in range(max(len(t) - SHINGLE + 1, 1)):
        m.update(t[i : i + SHINGLE].encode())
    return m


def run(days: int = 3, run_id: str | None = None) -> dict:
    run_id = run_id or lake.new_run_id()
    threshold = load_yaml("pipeline.yaml")["wire"]["minhash_threshold"]
    settings = ingest_settings()
    rows = lake.rows(
        f"""
        WITH a AS (
            SELECT article_id, outlet_id, body, byline FROM {lake.source('articles', days)}
            QUALIFY row_number() OVER (PARTITION BY article_id ORDER BY created_at) = 1
        ),
        f AS (SELECT article_id, is_wire_copy FROM {lake.latest('article_flags', days=days + 1)})
        SELECT a.*, f.is_wire_copy AS prev FROM a LEFT JOIN f USING (article_id)
        """
    )
    cna = [r for r in rows if r["outlet_id"] == "cna"]
    lsh = MinHashLSH(threshold=threshold, num_perm=PERM)
    hashes = {}
    for r in cna:
        h = _minhash(clean_body(r["body"], settings.outlet("cna").self_names))
        hashes[r["article_id"]] = h
        lsh.insert(r["article_id"], h)
    out = []
    for r in rows:
        if r["outlet_id"] == "cna":
            continue
        body = clean_body(r["body"], settings.outlet(r["outlet_id"]).self_names)
        marker = (
            bool(_MARKER.search(r["body"][:300]))
            or bool(_TAIL.search(r["body"].strip()[-60:]))
            or any("中央社" in (b or "") for b in (r["byline"] or []))
        )
        h = _minhash(body)
        best, sim = None, 0.0
        for cid in lsh.query(h):
            s = h.jaccard(hashes[cid])
            if s > sim:
                best, sim = cid, s
        is_wire = marker or sim >= threshold
        if r["prev"] is not None and bool(r["prev"]) == is_wire:
            continue  # unchanged; CNA copy that arrives later still flips earlier articles
        out.append({"article_id": r["article_id"], "is_wire_copy": is_wire, "wire_of": best if sim >= threshold else None,
                    "wire_similarity": round(sim, 3), "created_at": lake.now_utc(), "run_id": run_id})
    lake.write("article_flags", out, run_id)
    return {"checked": len(out), "wire_copies": sum(1 for r in out if r["is_wire_copy"])}
