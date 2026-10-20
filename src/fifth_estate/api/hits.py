"""Anonymous page-view counting for the internal dashboard: no cookies, no third-party script.

The site sends one beacon per page (POST /v1/hit with {"route": "/events", "referrer": document.referrer}).
Each accepted hit becomes one JSON line on stdout, which Cloud Run stores as a log entry:

    {"severity":"INFO","message":"hit","event":"hit","day":"2026-10-19","route":"/events",
     "referrer":"facebook.com","visitor":"3f9a0c…"}

visitor is the first 16 hex digits of sha256(daily salt + IP + user agent), as Plausible does it: the same
browser counts once per day but cannot be followed from one day to the next. The daily salt is
HMAC-SHA256(secret, Taipei date); the secret comes from Secret Manager (FIFTH_VISITOR_SECRET) so all
instances agree. The IP, the user agent, the secret and the salt are never written anywhere.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone

TAIPEI = timezone(timedelta(hours=8))  # no daylight saving time
MAX_BODY = 2048
BOTS = re.compile(
    r"bot|crawl|spider|slurp|archiver|headless|lighthouse|pagespeed|pingdom|uptime|monitor|preview|"
    r"facebookexternalhit|embedly|curl|wget|python|httpx|aiohttp|go-http|java/|okhttp|axios|node-fetch|"
    r"undici|scrapy|phantomjs|selenium|playwright|puppeteer",
    re.I,
)
# Our own pages: navigation inside the site is not a referral.
OWN_HOSTS = ("the-fifth-estate.org", "the-fifth-estate.web.app", "the-fifth-estate.firebaseapp.com", "huang-yu-chen.github.io",
             "localhost", "127.0.0.1")

_secret = os.environ.get("FIFTH_VISITOR_SECRET", "").encode() or secrets.token_bytes(32)  # local: per process


def visitor(ip: str, user_agent: str, day: str, secret: bytes | None = None) -> str:
    salt = hmac.new(secret or _secret, day.encode(), hashlib.sha256).digest()
    return hashlib.sha256(salt + ip.encode() + b"\n" + user_agent.encode()).hexdigest()[:16]


def referrer_host(referrer: object) -> str:
    """Hostname only; empty for direct visits and our own pages."""
    if not isinstance(referrer, str) or not referrer:
        return ""
    host = (urllib.parse.urlsplit(referrer).hostname or "").lower()
    if any(host == h or host.endswith("." + h) for h in OWN_HOSTS):
        return ""
    return host.removeprefix("www.")


def route_path(route: object) -> str | None:
    """The page's path in the site (hash router), without query string; None if it does not look like one."""
    if not isinstance(route, str) or not route.startswith("/"):
        return None
    path = urllib.parse.unquote(route.split("?", 1)[0].split("#", 1)[0])
    return path[:200] if path and not any(c in path for c in "\r\n\t") else None


def record(body: bytes, ip: str, user_agent: str, now: datetime | None = None) -> dict | None:
    """Log one page view; returns the entry, or None when the hit is not counted (bots, malformed beacons)."""
    if not user_agent or BOTS.search(user_agent) or len(body) > MAX_BODY:
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    route = route_path(data.get("route")) if isinstance(data, dict) else None
    if route is None:
        return None
    day = (now or datetime.now(timezone.utc)).astimezone(TAIPEI).date().isoformat()
    entry = {"severity": "INFO", "message": "hit", "event": "hit", "day": day, "route": route,
             "referrer": referrer_host(data.get("referrer")), "visitor": visitor(ip, user_agent, day)}
    print(json.dumps(entry, ensure_ascii=False), file=sys.stdout, flush=True)
    return entry
