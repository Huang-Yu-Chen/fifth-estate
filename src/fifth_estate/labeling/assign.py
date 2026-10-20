"""Which article a labeler gets next.

Every article should end up with `k` independent labels (skips don't count), and nobody gets the
same article twice. Articles others have already labeled come first, then articles open on someone
else's screen, so complete pairs accumulate and human–human agreement can be computed early.
Otherwise each labeler follows a personal random order, so no two labelers read the set in the same
sequence. An open article counts as taken for CLAIM_MINUTES, so two labelers don't both become its
second coder. k = 0 means everyone labels every article.

`labels`: labeler → {key: label}; `claims`: key → {labeler: claimed until, epoch seconds}.
"""

from __future__ import annotations

import hashlib
from collections import Counter

CLAIM_MINUTES = 60


def personal_order(keys: list[str], labeler: str, set_name: str) -> list[str]:
    return sorted(keys, key=lambda k: hashlib.sha256(f"{set_name}\n{labeler}\n{k}".encode()).digest())


def taken(key: str, labeler: str, labels: dict[str, dict], claims: dict[str, dict[str, float]], now: float) -> tuple[int, int]:
    """How many other labelers have labeled `key`, and how many have it open."""
    done = sum(1 for lab, mine in labels.items() if lab != labeler and key in mine and not mine[key].get("skipped"))
    return done, sum(1 for lab, until in claims.get(key, {}).items() if lab != labeler and until > now)


def next_key(keys: list[str], labeler: str, set_name: str, k: int, labels: dict[str, dict],
             claims: dict[str, dict[str, float]], now: float) -> str | None:
    mine = labels.get(labeler, {})
    order = personal_order([x for x in keys if x not in mine], labeler, set_name)
    for key in order:  # still open on this labeler's screen (a reload): the same one again
        if claims.get(key, {}).get(labeler, 0) > now:
            return key
    if k <= 0:
        return order[0] if order else None
    best: tuple[tuple[int, int], str] | None = None
    for key in order:
        done, held = taken(key, labeler, labels, claims, now)
        if done + held < k and (best is None or (done, held) > best[0]):
            best = ((done, held), key)
    return best[1] if best else None


def done_counts(labels: dict[str, dict]) -> Counter:
    """key → number of (non-skipped) labels."""
    return Counter(key for mine in labels.values() for key, lab in mine.items() if not lab.get("skipped"))


def needed(keys: list[str], k: int, labels: dict[str, dict]) -> int | None:
    """Labels still missing before every article has k of them (None when everyone labels everything)."""
    if k <= 0:
        return None
    done = done_counts(labels)
    return sum(max(0, k - done[key]) for key in keys)
