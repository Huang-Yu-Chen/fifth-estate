"""Discovery: turn an outlet's sources (RSS, news sitemaps, listing pages, Yahoo) into candidate URLs."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import quote, urljoin

import feedparser
from lxml import etree, html

from ..config import Outlet, Source
from . import dates
from .fetch import Fetcher

log = logging.getLogger(__name__)

NS = {
    "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
    "news": "http://www.google.com/schemas/sitemap-news/0.9",
}
YAHOO_ARCHIVE = "https://tw.news.yahoo.com/{provider}--" + quote("所有類別") + "/archive/"
YAHOO_ARTICLE = re.compile(r"^https://tw\.news\.yahoo\.com/[^\"'\s?#]+-\d{9}\.html$")


@dataclass
class Candidate:
    url: str
    source: str
    title_hint: str | None = None
    published_hint: datetime | None = None
    keywords: list[str] = field(default_factory=list)


async def discover(fetcher: Fetcher, outlet: Outlet) -> tuple[list[Candidate], list[str]]:
    """Return (candidates, errors) across all of the outlet's sources."""
    out: list[Candidate] = []
    errors: list[str] = []
    for src in outlet.sources:
        try:
            found = await _discover_one(fetcher, outlet, src)
            log.info("%s %s %s -> %d", outlet.id, src.type, src.url or src.provider, len(found))
            out.extend(found)
        except Exception as e:  # one broken source must not stop the others
            msg = f"{outlet.id} {src.type} {src.url or src.provider}: {type(e).__name__}: {e}"
            log.warning(msg)
            errors.append(msg)
    return out, errors


async def _discover_one(fetcher: Fetcher, outlet: Outlet, src: Source) -> list[Candidate]:
    if src.type == "rss":
        return await _rss(fetcher, outlet, src)
    if src.type == "news_sitemap":
        return await _news_sitemap(fetcher, outlet, src.url, f"sitemap:{src.url}")
    if src.type == "sitemap_index":
        r = await fetcher.get(src.url)
        r.raise_for_status()
        root = etree.fromstring(r.content, parser=etree.XMLParser(recover=True, huge_tree=True))
        out: list[Candidate] = []
        for loc in root.findall(".//sm:sitemap/sm:loc", NS):
            out.extend(await _news_sitemap(fetcher, outlet, loc.text.strip(), f"sitemap:{src.url}"))
        return out
    if src.type == "listing":
        return await _listing(fetcher, src)
    if src.type == "yahoo_provider":
        return await _yahoo_provider(fetcher, src)
    raise ValueError(f"unknown source type {src.type}")


async def _rss(fetcher: Fetcher, outlet: Outlet, src: Source) -> list[Candidate]:
    r = await fetcher.get(src.url)
    r.raise_for_status()
    feed = feedparser.parse(r.content)
    out = []
    for e in feed.entries:
        link = e.get("link")
        if not link:
            continue
        if src.tags:
            tags = {t.get("term") for t in e.get("tags", [])}
            if not tags & set(src.tags):
                continue
        published = dates.parse(e.get("published") or e.get("updated"), outlet.date_quirk)
        out.append(Candidate(url=link, source=f"rss:{src.url}", title_hint=e.get("title"), published_hint=published))
    return out


async def _news_sitemap(fetcher: Fetcher, outlet: Outlet, url: str, label: str) -> list[Candidate]:
    r = await fetcher.get(url)
    r.raise_for_status()
    root = etree.fromstring(r.content, parser=etree.XMLParser(recover=True, huge_tree=True))
    out = []
    for el in root.findall(".//sm:url", NS):
        loc = (el.findtext("sm:loc", namespaces=NS) or "").strip()
        if not loc:
            continue
        title = el.findtext(".//news:title", namespaces=NS)
        pub = el.findtext(".//news:publication_date", namespaces=NS) or el.findtext("sm:lastmod", namespaces=NS)
        kw = el.findtext(".//news:keywords", namespaces=NS) or ""
        out.append(
            Candidate(
                url=loc,
                source=label,
                title_hint=title.strip() if title else None,
                published_hint=dates.parse(pub, outlet.date_quirk),
                keywords=[k.strip() for k in kw.split(",") if k.strip()],
            )
        )
    return out


async def _listing(fetcher: Fetcher, src: Source) -> list[Candidate]:
    r = await fetcher.get(src.url)
    r.raise_for_status()
    doc = html.document_fromstring(r.text)
    pat = re.compile(src.link_pattern)
    seen: dict[str, Candidate] = {}
    for a in doc.xpath("//a[@href]"):
        href = urljoin(str(r.url), a.get("href"))
        if pat.search(href) and href not in seen:
            text = " ".join(a.text_content().split())
            seen[href] = Candidate(url=href, source=f"listing:{src.url}", title_hint=text or None)
    return list(seen.values())


async def _yahoo_provider(fetcher: Fetcher, src: Source) -> list[Candidate]:
    url = YAHOO_ARCHIVE.format(provider=src.provider)
    r = await fetcher.get(url)
    r.raise_for_status()
    doc = html.document_fromstring(r.text)
    seen: dict[str, Candidate] = {}
    for a in doc.xpath("//a[@href]"):
        href = urljoin(str(r.url), a.get("href")).split("?")[0]
        if YAHOO_ARTICLE.match(href) and href not in seen:
            item = next((anc for anc in a.iterancestors() if anc.tag == "li"), None)
            if src.label and item is not None and src.label not in item.text_content():
                continue  # recommended items from other providers
            text = " ".join(a.text_content().split())
            seen[href] = Candidate(url=href, source=f"yahoo:{src.provider}", title_hint=text or None)
    return list(seen.values())
