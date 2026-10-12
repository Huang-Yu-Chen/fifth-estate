"""URL canonicalization and article IDs."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

# Query parameters that never identify an article.
_DROP_PARAMS = re.compile(r"^(utm_.*|from|fbclid|gclid|ref|ref_src|cid|ctrack|ns_.*|_gl|ga_.*|outputType|type|mode|via)$", re.I)


def canonicalize(url: str) -> str:
    """Normalize scheme/host, drop tracking params and fragments, collapse AMP paths."""
    parts = urlsplit(url.strip())
    scheme = "https"
    host = parts.netloc.lower()
    path = parts.path or "/"
    path = re.sub(r"/amp(/|$)", "/", path)  # news.ltn.com.tw/amp/news/... -> /news/...
    path = re.sub(r"/{2,}", "/", path)
    # Normalize percent-encoding so encoded and raw CJK paths compare equal.
    path = quote(unquote(path), safe="/:@!$&'()*+,;=-._~")
    if len(path) > 1 and path.endswith("/") and not host.endswith("yahoo.com"):
        path = path.rstrip("/")
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False) if not _DROP_PARAMS.match(k)]
    return urlunsplit((scheme, host, path, urlencode(sorted(query)), ""))


def article_id(outlet_id: str, canonical_url: str) -> str:
    return hashlib.sha1(f"{outlet_id}|{canonical_url}".encode()).hexdigest()[:16]


def matches_any(patterns: tuple[str, ...] | list[str], text: str) -> bool:
    return any(re.search(p, text) for p in patterns)
