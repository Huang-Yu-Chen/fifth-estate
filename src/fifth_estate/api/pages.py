"""The website's pages that depend on the data, rendered from the current snapshot: units (/unit/<kind>/<id>),
outlets (/outlet/<id>), sitemap.xml and robots.txt.

The rest of the website is static on Firebase Hosting, which sends these paths here (firebase.json). A unit that
appears after the last site deploy (a new event or subtopic) therefore still gets status 200 with its own title,
description, canonical URL and link-preview tags, and an unknown one gets 404, without redeploying the site.

The HTML comes from the site's page-templates.json (written by web/vite.config.ts with the wording of
web/src/site.ts, deployed with the site and read from there: FIFTH_SITE_TEMPLATES), so these pages always load
the scripts that are deployed. It is reread at most every CHECK_SECONDS, like the snapshot. Pages stay out of
search engines while the snapshot's site status is beta (config/pipeline.yaml) or its data is fake.
"""

from __future__ import annotations

import gzip
import hashlib
import html
import json
import os
import re
import sqlite3
import threading
import time
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from . import schema
from .snapshots import CHECK_SECONDS, RETRY_SECONDS, Snapshot, Store, log

SLUG = re.compile(r"[A-Za-z0-9_-]+")  # unit and outlet ids; anything else is not a page
HTML_CACHE = "public, max-age=300, s-maxage=600"  # s-maxage: Firebase Hosting's CDN
TEXT_CACHE = "public, max-age=600"
_SLOT = re.compile(r"@@(PATH|NAME|DESCRIPTION)@@")  # in the "page" templates
_FIELD = re.compile(r"\{(name|detail)\}")  # in the "describe" templates


class Templates:
    """page-templates.json from a URL (the deployed site) or a local file (`fifth serve`)."""

    def __init__(self, source: str, check_seconds: float = CHECK_SECONDS, clock=time.monotonic) -> None:
        self.source = source
        self.check_seconds = check_seconds
        self.clock = clock
        self.doc: dict | None = None
        self.version: str | None = None
        self.error: str | None = None
        self._refreshing = threading.Lock()
        self._next_check = 0.0

    @classmethod
    def from_env(cls) -> Templates:
        return cls(os.environ.get("FIFTH_SITE_TEMPLATES", "web/dist/page-templates.json"))

    def _read(self) -> bytes:
        if self.source.startswith(("https://", "http://")):
            req = urllib.request.Request(self.source, headers={"Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.read()
        return Path(self.source).read_bytes()

    def refresh(self) -> bool:
        """Load the templates unless they are the ones in hand. True if new ones were loaded."""
        with self._refreshing:
            self._next_check = self.clock() + self.check_seconds
            try:
                body = self._read()
                version = hashlib.sha256(body).hexdigest()[:12]
                if version == self.version:
                    self.error = None
                    return False
                doc = json.loads(body)
                _check(doc)
            except Exception as e:
                self.error = f"{type(e).__name__}: {e}"[:500]
                if self.doc is None:
                    self._next_check = self.clock() + RETRY_SECONDS
                log("ERROR" if self.doc is None else "WARNING", "page templates not loaded", source=self.source, error=self.error)
                return False
            doc["_version"] = version  # travels with the doc, so a response never mixes two versions
            self.doc, self.version, self.error = doc, version, None
            log("INFO", "page templates loaded", source=self.source, version=version)
            return True

    def current(self) -> dict | None:
        """The templates in hand. Loads them if there are none; otherwise starts a background check when due."""
        now = self.clock()
        if self.doc is None:
            if now >= self._next_check:
                self.refresh()
        elif now >= self._next_check and not self._refreshing.locked():
            self._next_check = now + self.check_seconds
            threading.Thread(target=self.refresh, name="templates-refresh", daemon=True).start()
        return self.doc


def _check(doc: object) -> None:
    """Refuse anything else, so a broken file never replaces working templates."""
    try:
        ok = (
            isinstance(doc["site_url"], str)
            and isinstance(doc["pages"], list)
            and isinstance(doc["not_found"], str)
            and all("@@PATH@@" in doc["page"][k] for k in ("index", "noindex"))
            and all(isinstance(doc["describe"][k], str) for k in ("event", "unit", "unit_plain", "outlet"))
        )
    except (KeyError, TypeError):
        ok = False
    if not ok:
        raise ValueError("not a page-templates.json (web/vite.config.ts)")


def describe(t: dict, kind: str, name: str, detail: str | None = None) -> str:
    """The page description, worded as in web/src/site.ts (unitDescription, outletDescription)."""
    key = kind if kind in ("event", "outlet") else "unit" if detail else "unit_plain"
    values = {"name": name, "detail": detail or ""}
    return _FIELD.sub(lambda m: values[m.group(1)], t["describe"][key])


def render(t: dict, *, path: str, name: str, description: str, indexable: bool) -> str:
    values = {"PATH": html.escape(path), "NAME": html.escape(name), "DESCRIPTION": html.escape(description)}
    return _SLOT.sub(lambda m: values[m.group(1)], t["page"]["index" if indexable else "noindex"])


def _doc(con: sqlite3.Connection, path: str) -> dict | None:
    row = con.execute("SELECT gz FROM docs WHERE path = ?", (path,)).fetchone()
    return json.loads(gzip.decompress(row[0])) if row else None


def router(store: Store, templates: Templates) -> APIRouter:
    r = APIRouter(include_in_schema=False)
    cache: tuple[str, dict, bool] | None = None  # snapshot id, its meta, indexable

    def site(snap: Snapshot, con: sqlite3.Connection) -> tuple[dict, bool]:
        """The snapshot's meta, and whether its pages may be indexed (the same rule as web/vite.config.ts)."""
        nonlocal cache
        c = cache
        if c is None or c[0] != snap.id:
            meta = _doc(con, "meta") or {}
            indexable = bool(meta) and not meta.get("fake_data") and (meta.get("site") or {}).get("status") != "beta"
            c = cache = (snap.id, meta, indexable)
        return c[1], c[2]

    def need_templates() -> dict:
        t = templates.current()
        if t is None:
            raise HTTPException(503, f"page templates not loaded: {templates.error}", headers={"Retry-After": "30"})
        return t

    def page(request: Request, rest: str, find) -> Response:
        """`rest` is the path after /unit/ or /outlet/; `find(con, rest, t)` gives (path, name, description) or None."""
        if rest.endswith("/") and rest.strip("/"):  # a relative redirect: requests arrive through the site's domain
            query = f"?{request.url.query}" if request.url.query else ""
            return RedirectResponse(request.url.path.rstrip("/") + query, status_code=301)
        t = need_templates()
        with store.reader() as (snap, con):
            found = find(con, rest, t)
            _, indexable = site(snap, con)
        if found is None:
            return HTMLResponse(t["not_found"], status_code=404, headers={"Cache-Control": "no-store"})
        etag = f'W/"{snap.id}.{t["_version"]}"'
        headers = {"ETag": etag, "Cache-Control": HTML_CACHE}
        if etag in request.headers.get("if-none-match", ""):
            return Response(status_code=304, headers=headers)
        path, name, description = found
        return HTMLResponse(render(t, path=path, name=name, description=description, indexable=indexable), headers=headers)

    def find_unit(con: sqlite3.Connection, rest: str, t: dict) -> tuple[str, str, str] | None:
        kind, _, uid = rest.partition("/")
        doc = _doc(con, schema.unit_doc(kind, uid)) if kind in schema.UNIT_KINDS and SLUG.fullmatch(uid) else None
        if doc is None:
            return None
        return f"/unit/{kind}/{uid}", doc["name"], describe(t, kind, doc["name"], doc.get("description"))

    def find_outlet(con: sqlite3.Connection, rest: str, t: dict) -> tuple[str, str, str] | None:
        doc = _doc(con, schema.outlet_doc(rest)) if SLUG.fullmatch(rest) else None
        return None if doc is None else (f"/outlet/{rest}", doc["name"], describe(t, "outlet", doc["name"]))

    @r.api_route("/unit", methods=["GET", "HEAD"])
    @r.api_route("/unit/{rest:path}", methods=["GET", "HEAD"])
    def unit_page(request: Request, rest: str = "") -> Response:
        return page(request, rest, find_unit)

    @r.api_route("/outlet", methods=["GET", "HEAD"])
    @r.api_route("/outlet/{rest:path}", methods=["GET", "HEAD"])
    def outlet_page(request: Request, rest: str = "") -> Response:
        return page(request, rest, find_outlet)

    @r.api_route("/robots.txt", methods=["GET", "HEAD"])
    def robots() -> Response:
        t = need_templates()
        with store.reader() as (snap, con):
            _, indexable = site(snap, con)
        body = "User-agent: *\nAllow: /\n" + (f"\nSitemap: {t['site_url']}/sitemap.xml\n" if indexable else "")
        return PlainTextResponse(body, headers={"Cache-Control": TEXT_CACHE})

    @r.api_route("/sitemap.xml", methods=["GET", "HEAD"])
    def sitemap() -> Response:
        """Every page once the site may be indexed; none (404) while it is in beta."""
        t = need_templates()
        with store.reader() as (snap, con):
            meta, indexable = site(snap, con)
            index = _doc(con, "index") if indexable else None
        if not indexable:
            return PlainTextResponse("no sitemap while the site is in beta\n", status_code=404, headers={"Cache-Control": TEXT_CACHE})
        paths = [
            *t["pages"],
            *(f"/unit/{kind}/{u['id']}" for kind, units in (index or {}).items() if kind in schema.UNIT_KINDS
              for u in units or [] if SLUG.fullmatch(str(u.get("id", "")))),
            *(f"/outlet/{o['id']}" for o in meta.get("outlets", []) if SLUG.fullmatch(str(o.get("id", "")))),
        ]
        urls = "\n".join(f"  <url><loc>{xml_escape(t['site_url'] + p)}</loc></url>" for p in paths)
        body = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urls}\n</urlset>\n'
        return Response(body, media_type="application/xml", headers={"Cache-Control": TEXT_CACHE})

    return r
