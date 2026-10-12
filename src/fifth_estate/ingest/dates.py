"""Timestamp parsing with per-outlet quirks (all results are timezone-aware UTC)."""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from ..config import TAIPEI

_ISO_LOOSE = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})[T ](?P<time>\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?)\s*(?P<tz>Z|[+-]\d{2}:?\d{2})?",
)


def parse(value: str | None, quirk: str | None = None) -> datetime | None:
    """Parse ISO-8601-ish, RFC-822 (RSS) or 'YYYY-MM-DD HH:MM +00:00' strings.

    quirk="naive_local": ignore any stated offset and treat the clock time as Taipei time
    (三立's JSON-LD labels Taipei time as +00:00).
    Naive timestamps are assumed to be Taipei time.
    """
    if not value:
        return None
    s = html.unescape(str(value)).strip()  # 太報 escapes '+' as &#43;
    dt: datetime | None = None
    m = _ISO_LOOSE.search(s)
    if m:
        tz = m.group("tz")
        iso = f"{m.group('date')}T{m.group('time')}"
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            dt = None
        if dt is not None and tz and quirk != "naive_local":
            if tz == "Z":
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                tz = tz if ":" in tz else f"{tz[:3]}:{tz[3:]}"
                dt = datetime.fromisoformat(f"{iso}{tz}")
    if dt is None:
        try:
            dt = parsedate_to_datetime(re.sub(r"\s+", " ", s.replace(",", ", ")))
        except (TypeError, ValueError, IndexError):
            return None
        if quirk == "naive_local":
            dt = dt.replace(tzinfo=None)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TAIPEI)
    return dt.astimezone(timezone.utc)


def plausible(dt: datetime | None, now: datetime | None = None, max_future_hours: float = 2.0) -> bool:
    """Reject timestamps in the future (e.g. Yahoo RSS labels UTC as +0800) or absurdly old."""
    if dt is None:
        return False
    now = now or datetime.now(timezone.utc)
    hours = (dt - now).total_seconds() / 3600
    return -24 * 365 * 5 < hours <= max_future_hours
