"""Batch API across scheduled runs: submit in one run, collect in a later one.

Pending batches live in the data repo (state/batches.json) so any runner can collect them.
"""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI

from ..store import lake, state

log = logging.getLogger(__name__)
STATE = "batches.json"
TERMINAL_FAILED = {"failed", "expired", "cancelled"}


@dataclass
class BatchLine:
    batch_id: str
    custom_id: str
    body: dict[str, Any] | None  # the Response object, or None on error
    error: str | None


def pending(task: str | None = None) -> list[dict]:
    return [b for b in state.load(STATE, default=[]) if b["status"] == "submitted" and (task is None or b["task"] == task)]


def pending_ids(task: str) -> set[str]:
    return {cid for b in pending(task) for cid in b.get("custom_ids", [])}


def submit(client: OpenAI, task: str, requests: list[tuple[str, dict[str, Any]]], meta: dict[str, Any]) -> str | None:
    if not requests:
        return None
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
        for cid, body in requests:
            f.write(json.dumps({"custom_id": cid, "method": "POST", "url": "/v1/responses", "body": body}, ensure_ascii=False) + "\n")
        path = Path(f.name)
    try:
        with path.open("rb") as fh:
            up = client.files.create(file=fh, purpose="batch")
        b = client.batches.create(
            input_file_id=up.id,
            endpoint="/v1/responses",
            completion_window="24h",
            metadata={"task": task, **{k: str(v)[:500] for k, v in meta.items()}},
        )
    finally:
        path.unlink(missing_ok=True)
    rec = {
        "id": b.id,
        "task": task,
        "status": "submitted",
        "created_at": lake.now_utc().isoformat(),
        "n": len(requests),
        "custom_ids": [cid for cid, _ in requests],
        **meta,
    }
    state.save(STATE, [*state.load(STATE, default=[]), rec])
    log.info("submitted batch %s (%s, %d requests)", b.id, task, len(requests))
    return b.id


def _read_file(client: OpenAI, file_id: str | None) -> list[dict]:
    if not file_id:
        return []
    text = client.files.content(file_id).text
    return [json.loads(ln) for ln in text.splitlines() if ln.strip()]


def collect(client: OpenAI, task: str) -> tuple[list[BatchLine], list[dict]]:
    """Return (lines from finished batches, batch records that finished). Unfinished batches are left alone."""
    records = state.load(STATE, default=[])
    lines: list[BatchLine] = []
    finished: list[dict] = []
    for rec in records:
        if rec["task"] != task or rec["status"] != "submitted":
            continue
        b = client.batches.retrieve(rec["id"])
        if b.status not in ("completed", *TERMINAL_FAILED):
            log.info("batch %s still %s (%s)", b.id, b.status, b.request_counts)
            continue
        seen: set[str] = set()
        for row in _read_file(client, b.output_file_id):
            resp = row.get("response") or {}
            ok = resp.get("status_code") == 200
            lines.append(BatchLine(b.id, row["custom_id"], resp.get("body") if ok else None, None if ok else json.dumps(resp.get("body"))[:500]))
            seen.add(row["custom_id"])
        for row in _read_file(client, b.error_file_id):
            if row["custom_id"] not in seen:
                lines.append(BatchLine(b.id, row["custom_id"], None, json.dumps(row.get("error") or row.get("response"))[:500]))
                seen.add(row["custom_id"])
        # Requests that never came back (expired/cancelled batches) are simply resubmitted later.
        rec["status"] = "collected" if b.status == "completed" else b.status
        rec["finished_at"] = lake.now_utc().isoformat()
        rec["returned"] = len(seen)
        rec.pop("custom_ids", None)
        finished.append(rec)
    if finished:
        # Keep the state file small: drop records older than 14 days.
        cutoff = (lake.now_utc().timestamp()) - 14 * 86400
        records = [r for r in records if r["status"] == "submitted" or _ts(r.get("finished_at")) > cutoff]
        state.save(STATE, records)
    return lines, finished


def _ts(iso: str | None) -> float:
    from datetime import datetime

    try:
        return datetime.fromisoformat(iso).timestamp() if iso else 0.0
    except ValueError:
        return 0.0
