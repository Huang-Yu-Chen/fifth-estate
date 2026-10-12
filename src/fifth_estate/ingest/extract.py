"""Article page -> title, timestamp, section path, body text, byline."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urljoin, urlsplit

import trafilatura
from lxml import html

from ..config import Outlet
from . import dates

ARTICLE_TYPES = {"NewsArticle", "Article", "ReportageNewsArticle", "AnalysisNewsArticle", "OpinionNewsArticle", "BlogPosting"}
_YAHOO_PROVIDER = re.compile(r'providerId\\?"\s*:\s*\\?"([^"\\]+)')


@dataclass
class Extracted:
    title: str | None = None
    published_at: datetime | None = None
    section: str | None = None
    body: str = ""
    method: str = "none"
    byline: list[str] = field(default_factory=list)
    canonical_url: str | None = None
    provider_id: str | None = None


def _loads_lenient(s: str):
    try:
        return json.loads(s, strict=False)
    except json.JSONDecodeError:
        pass
    # ETtoday ships JSON-LD with // comments; some sites leave trailing commas.
    t = re.sub(r"(^|[\s,{\[])//[^\n]*", r"\1", s)
    t = re.sub(r",\s*([}\]])", r"\1", t)
    try:
        return json.loads(t, strict=False)
    except json.JSONDecodeError:
        return None


def _ld_items(doc) -> list[dict]:
    items: list[dict] = []
    for s in doc.xpath('//script[@type="application/ld+json"]/text()'):
        d = _loads_lenient(s)
        stack = d if isinstance(d, list) else [d]
        while stack:
            it = stack.pop(0)
            if isinstance(it, list):
                stack.extend(it)
            elif isinstance(it, dict):
                if "@graph" in it:
                    stack.extend(it["@graph"] if isinstance(it["@graph"], list) else [it["@graph"]])
                items.append(it)
    return items


def _types(it: dict) -> set[str]:
    t = it.get("@type")
    return set(t) if isinstance(t, list) else {t} if t else set()


def _strip_tags(s: str) -> str:
    if "<" not in s:
        return s
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</h\d>", "\n", s)
    return html.fragment_fromstring(f"<div>{s}</div>").text_content()


def _norm_text(s: str) -> str:
    lines = [re.sub(r"[ \t　\xa0]+", " ", ln).strip() for ln in s.replace("\r", "").split("\n")]
    return "\n".join(ln for ln in lines if ln)


def _crumb_path(names: list[str], title: str | None) -> list[str]:
    """Breadcrumb names minus the leading site/home crumb and a trailing headline crumb."""
    out: list[str] = []
    for i, n in enumerate(names):
        n = " ".join(str(n or "").split())
        if not n or (i == 0 and len(names) > 1):
            continue
        if title and (n == title or len(n) > 14):
            continue
        if n not in out:
            out.append(n)
    return out


def _clean_title(title: str, self_names: tuple[str, ...]) -> str:
    """Strip trailing ' | 媒體名' / ' - 媒體名' / ' | 政治' segments (repeatedly)."""
    words = [re.escape(n) for n in self_names] + ["政治", "國際", "兩岸", "要聞", "財經", "社會", "生活", "新聞"]
    seg = re.compile(r"\s*[|｜]\s*[^|｜]*(" + "|".join(words) + r")[^|｜]*$|\s+[-–—]\s+[^-–—]*(" + "|".join(words) + r")[^-–—]*$")
    prev = None
    while prev != title:
        prev, title = title, seg.sub("", title).strip()
    return title


def extract(page_html: str, url: str, outlet: Outlet) -> Extracted:
    doc = html.document_fromstring(page_html)
    ex = Extracted()
    lds = _ld_items(doc)
    art = next((it for it in lds if _types(it) & ARTICLE_TYPES), {})
    crumbs_ld = next((it for it in lds if "BreadcrumbList" in _types(it)), None)
    meta = {}
    for m in doc.xpath("//meta"):
        k = m.get("property") or m.get("name") or m.get("itemprop")
        if k and m.get("content") and k not in meta:
            meta[k] = m.get("content").strip()

    # --- title
    title = art.get("headline") or meta.get("og:title") or (doc.findtext(".//title") or "")
    title = _clean_title(" ".join(str(title).split()), outlet.self_names)
    ex.title = title or None

    # --- timestamp
    for cand in (meta.get("article:published_time"), art.get("datePublished"), meta.get("pubdate")):
        dt = dates.parse(cand, outlet.date_quirk)
        if dates.plausible(dt):
            ex.published_at = dt
            break

    # --- section path
    paths: list[list[str]] = []
    html_crumbs = [" ".join(a.text_content().split()) for a in doc.xpath('//*[contains(@class,"breadcrumb")]//a')]
    ld_crumbs = []
    if crumbs_ld:
        for e in crumbs_ld.get("itemListElement", []) or []:
            if isinstance(e, dict):
                item = e.get("item")
                ld_crumbs.append(e.get("name") or (item.get("name") if isinstance(item, dict) else None) or "")
    sec = art.get("articleSection")
    sec_list = [sec] if isinstance(sec, str) else list(sec or [])
    if outlet.id == "storm":  # LD says only "新聞"; the visible breadcrumb carries 政治/國際
        paths = [_crumb_path(html_crumbs, ex.title), _crumb_path(ld_crumbs, ex.title)]
    else:
        paths = [_crumb_path(ld_crumbs, ex.title), sec_list, [meta.get("article:section", "")], _crumb_path(html_crumbs, ex.title)]
    for p in paths:
        p = [x for x in p if x]
        if p:
            ex.section = ">".join(p)
            break

    # --- byline (kept for wire detection; never sent to the LLM)
    authors = art.get("author") or []
    for a in authors if isinstance(authors, list) else [authors]:
        name = a.get("name") if isinstance(a, dict) else a
        if name and isinstance(name, str):
            ex.byline.append(name.strip())

    # --- canonical URL
    canon = doc.xpath('//link[@rel="canonical"]/@href')
    if canon:
        c = urljoin(url, canon[0].strip())
        if urlsplit(c).netloc.split(".")[-2:] == urlsplit(url).netloc.split(".")[-2:]:
            ex.canonical_url = c

    # --- body
    if outlet.via == "yahoo":
        m = _YAHOO_PROVIDER.search(page_html)
        ex.provider_id = m.group(1) if m else None
        arts = doc.xpath('//article[starts-with(@id,"article-")]')
        if arts:
            paras = [" ".join(p.text_content().split()) for p in arts[0].xpath('.//div[contains(@class,"atoms")]//p')]
            body = "\n".join(p for p in paras if p and not re.match(r"^【?(看原文連結|延伸閱讀|更多)", p))
            if body:
                ex.body, ex.method = body, "selector"
                return ex
    ld_body = _norm_text(_strip_tags(art.get("articleBody") or ""))
    traf = trafilatura.extract(page_html, favor_precision=True, include_comments=False, include_tables=False) or ""
    traf = _norm_text(traf)
    if ex.title and traf.startswith(ex.title):
        traf = traf[len(ex.title):].lstrip()
    if ld_body and len(ld_body) >= 0.7 * len(traf):
        ex.body, ex.method = ld_body, "jsonld"
    elif traf:
        ex.body, ex.method = traf, "trafilatura"
    return ex
