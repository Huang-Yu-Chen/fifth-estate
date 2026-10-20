"""The labeling site (FastAPI). Runs on Cloud Run with FIFTH_LABEL_STORE=gs://…, or locally through
`fifth labeling serve`. It imports nothing from the pipeline, so its container needs only the
`labeling-server` dependency group.

Blinding: a browser only ever receives an article's random key, its blinded title and body, the
codebook, and the labeler's own labels. Article ids (which the public site's data could map to an
outlet and model scores), outlets, URLs, dates, model output and other labelers' labels stay here.

Login: every labeler has a personal link …/#join=<token>. The token sits in the URL fragment, so it
never reaches request logs or Referer headers; the page posts it to /api/login, which sets it as an
HttpOnly cookie. Only its SHA-256 is stored, and revoking a labeler (or reissuing the link) ends the
old one within REFRESH_SECONDS.

Deploy with max-instances 1: which article is open on whose screen (the claims) lives in memory.
Writes are still conditional on the version read, so an overlapping instance during a rollout
cannot lose labels.
"""

from __future__ import annotations

import hashlib
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import assign
from . import store as st
from .form import Label

STATIC = Path(__file__).parent / "static"
COOKIE = "fe_label"
COOKIE_DAYS = 180
REFRESH_SECONDS = 30  # how long cached labelers, sets and labels are trusted (CLI changes show up within this)
HEADERS = {
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Site:
    """Cached view of the store, plus the claims (which article is open on whose screen)."""

    def __init__(self, store: st.Store):
        self.store = store
        self.lock = threading.RLock()
        self.claims: dict[str, dict[str, dict[str, float]]] = {}  # set → key → labeler → until
        self._labelers: tuple[float, dict[str, dict]] = (0.0, {})  # (loaded at, token hash → labeler)
        self._sets: tuple[float, dict[str, tuple[int, dict]]] = (0.0, {})  # (loaded at, file → (version, bundle))
        self._labels: dict[str, tuple[float, dict[str, tuple[int, dict]]]] = {}  # set → (loaded at, labeler → (version, labels))

    def labeler(self, token: str | None, fresh: bool = False) -> dict | None:
        """The active labeler holding `token`. `fresh` re-reads labelers.json (at most every 2 s), so a
        link works right after `fifth labeling invite`."""
        if not token:
            return None
        with self.lock:
            at, by_hash = self._labelers
            age = time.time() - at
            if age > REFRESH_SECONDS or (fresh and age > 2):
                doc = st.load(self.store, "labelers.json", {"labelers": []})
                by_hash = {lab["token_sha256"]: lab for lab in doc["labelers"] if lab.get("active", True)}
                self._labelers = (time.time(), by_hash)
        return by_hash.get(token_hash(token))

    def sets(self) -> dict[str, dict]:
        """Open sets by name; bundles are re-downloaded only when their version changed."""
        with self.lock:
            at, files = self._sets
            if time.time() - at > REFRESH_SECONDS:
                fresh = {}
                for name, ver in self.store.list("sets/").items():
                    old = files.get(name)
                    if old and old[0] == ver:
                        fresh[name] = old
                    elif b := st.load(self.store, name):
                        b["by_key"] = {it["key"]: it for it in b["items"]}
                        fresh[name] = (ver, b)
                files = fresh
                self._sets = (time.time(), files)
        return {b["set"]: b for _, b in files.values() if b.get("active", True)}

    def labels(self, set_name: str) -> dict[str, dict]:
        """labeler → {key: label} for one set."""
        with self.lock:
            at, files = self._labels.get(set_name, (0.0, {}))
            if time.time() - at > REFRESH_SECONDS:
                fresh = {}
                for name, ver in self.store.list(f"labels/{set_name}/").items():
                    labeler = name.rsplit("/", 1)[-1].removesuffix(".json")
                    old = files.get(labeler)
                    fresh[labeler] = old if old and old[0] == ver else (ver, (st.load(self.store, name) or {}).get("labels", {}))
                files = fresh
                self._labels[set_name] = (time.time(), files)
            return {lab: labels for lab, (_, labels) in files.items()}

    def next_key(self, bundle: dict, labeler: str) -> str | None:
        name = bundle["set"]
        with self.lock:
            claims = self.claims.setdefault(name, {})
            now = time.time()
            key = assign.next_key(list(bundle["by_key"]), labeler, name, bundle["coders_per_article"], self.labels(name), claims, now)
            for holders in claims.values():  # one open article per labeler and set
                holders.pop(labeler, None)
            if key:
                claims.setdefault(key, {})[labeler] = now + assign.CLAIM_MINUTES * 60
            return key

    def holds(self, set_name: str, key: str, labeler: str) -> bool:
        return self.claims.get(set_name, {}).get(key, {}).get(labeler, 0) > time.time()

    def save(self, set_name: str, labeler: str, key: str, label: Label, codebook_version: str) -> dict:
        stamp = utc_now()
        new = label.model_dump()

        def change(doc: dict) -> dict:
            prev = doc["labels"].get(key) or {}
            spent = [s for s in (prev.get("seconds"), new["seconds"]) if s is not None]
            doc["labels"][key] = {
                **new,
                "seconds": sum(spent) if spent else None,  # over all revisions
                "labeled_at": stamp,
                "first_labeled_at": prev.get("first_labeled_at", stamp),
                "rev": prev.get("rev", 0) + 1,
                "codebook": codebook_version,
            }
            return doc

        with self.lock:
            doc, ver = st.update(self.store, f"labels/{set_name}/{labeler}.json", change, {"set": set_name, "labeler": labeler, "labels": {}})
            at, files = self._labels.get(set_name, (0.0, {}))
            self._labels[set_name] = (at, {**files, labeler: (ver, doc["labels"])})
            self.claims.get(set_name, {}).get(key, {}).pop(labeler, None)
        return doc["labels"][key]


class Login(BaseModel):
    token: str = Field(max_length=200)


def _item(bundle: dict, key: str) -> dict:
    it = bundle["by_key"][key]
    return {"key": key, "title": it["title"], "body": it["body"]}


def create_app(store: st.Store | None = None) -> FastAPI:
    site = Site(store or st.open_store())
    app = FastAPI(title="第五權 標註站", docs_url=None, redoc_url=None, openapi_url=None)

    def secure(request: Request, response: Response) -> Response:
        response.headers.update(HEADERS)
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # Writes must come from this site's own page (blocks cross-site form posts using the cookie).
        if request.method not in ("GET", "HEAD"):
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return secure(request, JSONResponse({"detail": "cross-site request"}, status_code=403))
            if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
                return secure(request, JSONResponse({"detail": "expected JSON"}, status_code=415))
        return secure(request, await call_next(request))

    def current(request: Request) -> dict:
        lab = site.labeler(request.cookies.get(COOKIE))
        if not lab:
            raise HTTPException(401, "請用你的個人連結開啟")
        return lab

    def open_set(name: str, lab: dict) -> dict:
        b = site.sets().get(name)
        if not b or (lab.get("sets") is not None and name not in lab["sets"]):
            raise HTTPException(404, "沒有這個標註組")
        return b

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/robots.txt", response_class=PlainTextResponse)
    def robots() -> str:
        return "User-agent: *\nDisallow: /\n"

    @app.get("/health", response_class=PlainTextResponse)  # not /healthz: Cloud Run reserves paths ending in z
    def health() -> str:
        return "ok"

    @app.post("/api/login")
    def login(body: Login, request: Request, response: Response) -> dict:
        lab = site.labeler(body.token, fresh=True)
        if not lab:
            raise HTTPException(403, "這個連結無效或已停用")
        response.set_cookie(COOKIE, body.token, max_age=COOKIE_DAYS * 86400, httponly=True, samesite="strict",
                            secure=request.url.scheme == "https", path="/")
        return {"name": lab["name"]}

    @app.post("/api/logout")
    def logout(response: Response) -> dict:
        response.delete_cookie(COOKIE, path="/")
        return {}

    @app.get("/api/me")
    def me(lab: dict = Depends(current)) -> dict:
        out = []
        for name, b in sorted(site.sets().items()):
            if lab.get("sets") is not None and name not in lab["sets"]:
                continue
            labels = site.labels(name)
            mine = [v for k, v in labels.get(lab["id"], {}).items() if k in b["by_key"]]
            keys = list(b["by_key"])
            out.append({
                "set": name,
                "title": b.get("title") or name,
                "items": len(keys),
                "coders_per_article": b["coders_per_article"],
                "done": sum(1 for v in mine if not v.get("skipped")),
                "skipped": sum(1 for v in mine if v.get("skipped")),
                "needed": assign.needed(keys, b["coders_per_article"], labels),
            })
        return {"id": lab["id"], "name": lab["name"], "sets": out}

    @app.post("/api/sets/{name}/next")
    def next_item(name: str, lab: dict = Depends(current)) -> dict:
        b = open_set(name, lab)
        key = site.next_key(b, lab["id"])
        return {"item": _item(b, key) if key else None}

    @app.get("/api/sets/{name}/items/{key}")
    def item(name: str, key: str, lab: dict = Depends(current)) -> dict:
        """An article the labeler has open or has labeled before (to revise it)."""
        b = open_set(name, lab)
        mine = site.labels(name).get(lab["id"], {})
        if key not in b["by_key"] or (key not in mine and not site.holds(name, key, lab["id"])):
            raise HTTPException(404, "沒有這篇")
        return {"item": _item(b, key), "label": mine.get(key)}

    @app.get("/api/sets/{name}/mine")
    def mine(name: str, lab: dict = Depends(current)) -> dict:
        b = open_set(name, lab)
        rows = [{"key": k, "title": b["by_key"][k]["title"], "labeled_at": v["labeled_at"], "skipped": bool(v.get("skipped"))}
                for k, v in site.labels(name).get(lab["id"], {}).items() if k in b["by_key"]]
        return {"labels": sorted(rows, key=lambda r: r["labeled_at"], reverse=True)}

    @app.put("/api/sets/{name}/labels/{key}")
    def save(name: str, key: str, body: Label, lab: dict = Depends(current)) -> dict:
        b = open_set(name, lab)
        if key not in b["by_key"]:
            raise HTTPException(404, "沒有這篇")
        return {"label": site.save(name, lab["id"], key, body, b.get("codebook_version", ""))}

    @app.get("/api/sets/{name}/codebook")
    def codebook(name: str, lab: dict = Depends(current)) -> dict:
        b = open_set(name, lab)
        return {"note": b.get("codebook_note", ""), "sections": b["codebook"]}

    return app
