"""What happens when the OpenAI quota runs out.

Crawling needs no OpenAI, so ingest keeps going. Annotation stops at the first quota error; nothing is
recorded for the articles it could not label, so none of them loses an attempt. Every later hourly run
makes one tiny paid request to see whether the quota is back. Once it is, the backlog is labeled (newest
first, looking back to the start of the outage) and publish resumes when nothing is left, so the website
never shows half-labeled days (they would look like thin coverage and low polarization).

    ok ──quota error──▶ paused ──quota back──▶ catching_up ──backlog done──▶ ok
                          ▲                         │
                          └──────quota error────────┘

The state lives in the data repo (state/pause.json) so every runner sees it. Only `fifth annotate` writes
it; publish, `fifth status` and the weekly report read it.
"""

from __future__ import annotations

from datetime import datetime

from ..config import TAIPEI, load_yaml
from ..store import lake, state

STATE = "pause.json"
LOOKBACK_DAYS = 7  # how far back annotation looks for unlabeled articles in normal operation


def load() -> dict:
    return state.load(STATE, default={"status": "ok"})


def site_paused(st: dict | None = None) -> bool:
    """Publish leaves the website as it is until the backlog is labeled."""
    return (st or load())["status"] != "ok"


def lookback_days(st: dict | None = None) -> int:
    """7 days normally; while paused or catching up, back to 7 days before the outage began (at most as far
    as the website's longest window reaches)."""
    st = st or load()
    if st["status"] == "ok":
        return LOOKBACK_DAYS
    began = datetime.fromisoformat(st["since"]).astimezone(TAIPEI).date()
    longest = max(load_yaml("pipeline.yaml")["windows"]) + 5
    return min((lake.taipei_today() - began).days + LOOKBACK_DAYS, longest)


def local(iso: str | None) -> str:
    """'10-20 14:07' in Taipei time."""
    return datetime.fromisoformat(iso).astimezone(TAIPEI).strftime("%m-%d %H:%M") if iso else "—"


def _now() -> str:
    return lake.now_utc().isoformat(timespec="seconds")


def pause(error: str) -> dict:
    """The quota ran out. Running out again while catching up keeps the first start, so the look-back
    still covers everything not yet labeled."""
    st = load()
    now = _now()
    st = {"status": "paused", "since": st.get("since") or now, "checked_at": now, "error": error[:500]}
    state.save(STATE, st)
    return st


def still_paused(error: str) -> dict:
    st = load() | {"checked_at": _now(), "error": error[:500]}
    state.save(STATE, st)
    return st


def resume() -> dict:
    now = _now()
    st = load() | {"status": "catching_up", "resumed_at": now, "checked_at": now, "error": None}
    state.save(STATE, st)
    return st


def backlog(days: int, before: datetime | None = None) -> dict[str, int]:
    """Articles still waiting: no Stage 1 label yet (attempts left), in scope but never Stage 2-labeled
    (in a running batch only if fetched before `before`), or in scope without an embedding."""
    from ..cluster import embed
    from ..llm import batch
    from . import stage1, stage2

    return {
        "stage1": stage1.count_pending(days),
        "stage2": stage2.count_unlabeled(days, exclude=batch.pending_ids(stage2.TASK), before=before),
        "embed": embed.count_pending(days),
    }


def finish_run(days: int) -> dict:
    """After a catch-up run that hit no quota error: back to ok once everything fetched before the quota
    came back is labeled."""
    st = load()
    waiting = backlog(days, before=datetime.fromisoformat(st["resumed_at"]))
    if any(waiting.values()):
        st |= {"checked_at": _now(), "backlog": waiting}
    else:
        st = {"status": "ok", "last_pause": {"since": st.get("since"), "resumed_at": st.get("resumed_at"), "caught_up_at": _now()}}
    state.save(STATE, st)
    return st


def left(st: dict, unit: str = "", sep: str = ", ") -> str:
    """The backlog in pipeline order, e.g. 'stage1 1234, stage2 800'."""
    b = st.get("backlog") or {}
    return sep.join(f"{k} {b[k]}{unit}" for k in ("stage1", "stage2", "embed") if b.get(k))


def describe(st: dict | None = None) -> str:
    st = st or load()
    if st["status"] == "paused":
        return f"OpenAI quota used up since {local(st['since'])} (Taipei), last checked {local(st.get('checked_at'))}"
    if st["status"] == "catching_up":
        return f"OpenAI quota back since {local(st.get('resumed_at'))}, labeling the backlog ({left(st) or 'not counted yet'} left)"
    return "running normally"
