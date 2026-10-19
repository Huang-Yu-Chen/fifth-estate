"""第五權 public API: the website's data, served from the current snapshot (api/snapshots.py).

Read-only and anonymous. Responses carry the snapshot id as ETag; browsers reuse them for a minute and
then revalidate, so a new publish reaches visitors within about two minutes without redeploying anything.

    uvicorn fifth_estate.api.app:app     (Cloud Run: see Dockerfile; locally: `fifth serve`)
"""

from __future__ import annotations

import base64
import gzip
import json
import traceback
from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, Response

from . import hits, schema
from .snapshots import NoSnapshot, Store, log

CACHE = "public, max-age=60, stale-while-revalidate=300"


def _cursor(ts: int, article_id: str) -> str:
    return base64.urlsafe_b64encode(f"{ts}:{article_id}".encode()).decode().rstrip("=")


def _uncursor(cursor: str) -> tuple[int, str]:
    try:
        ts, _, article_id = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode().partition(":")
        return int(ts), article_id
    except ValueError as e:  # also binascii.Error and UnicodeDecodeError
        raise HTTPException(400, "bad cursor") from e


def _client_ip(request: Request) -> str:
    # Cloud Run's front end appends the caller's address to X-Forwarded-For; earlier entries are whatever
    # the caller sent, so take the last one.
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.rsplit(",", 1)[-1].strip()
    return request.client.host if request.client else ""


def create_app(store: Store | None = None) -> FastAPI:
    store = store or Store.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await anyio.to_thread.run_sync(store.refresh)  # before uvicorn takes traffic, so no cold request waits on it
        yield

    app = FastAPI(
        title="第五權 The Fifth Estate API",
        version="1",
        summary="台灣新聞媒體政治極化偵測器的公開資料（唯讀）。",
        description="Dashboard data: polarization metrics per unit and outlet, and the scored article lists. "
        "Titles, links, scores and short model reasons only; never article text.",
        lifespan=lifespan,
        redoc_url=None,
    )
    app.state.store = store

    # Middleware added later wraps what came before: errors become a 500 that still gets CORS headers.
    @app.middleware("http")
    async def unexpected(request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:  # one JSON log line (Cloud Run: severity ERROR) instead of a raw traceback
            log("ERROR", f"{request.method} {request.url.path}: {type(exc).__name__}: {exc}", traceback=traceback.format_exc()[-4000:])
            return JSONResponse({"detail": "internal error"}, status_code=500)

    app.add_middleware(GZipMiddleware, minimum_size=1024)  # article lists; documents are stored gzipped already
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], max_age=86400)

    @app.exception_handler(NoSnapshot)
    async def no_snapshot(request: Request, exc: NoSnapshot) -> JSONResponse:
        return JSONResponse({"detail": f"no data yet: {exc}"}, status_code=503, headers={"Retry-After": "30"})

    def not_modified(request: Request, etag: str) -> bool:
        return etag in request.headers.get("if-none-match", "")

    def document(request: Request, path: str) -> Response:
        with store.reader() as (snap, con):
            row = con.execute("SELECT gz FROM docs WHERE path = ?", (path,)).fetchone()
        if row is None:
            raise HTTPException(404, f"no such document: {path}")
        etag = f'W/"{snap.id}"'
        headers = {"ETag": etag, "Cache-Control": CACHE, "Vary": "Accept-Encoding"}
        if not_modified(request, etag):
            return Response(status_code=304, headers=headers)
        if "gzip" in request.headers.get("accept-encoding", ""):
            return Response(row[0], media_type="application/json", headers={**headers, "Content-Encoding": "gzip"})
        return Response(gzip.decompress(row[0]), media_type="application/json", headers=headers)

    @app.get("/", include_in_schema=False)
    def root() -> dict:
        return {"name": "第五權 The Fifth Estate API", "docs": "/docs", "site": "https://the-fifth-estate.org"}

    @app.get("/v1/health", tags=["status"])
    def health() -> JSONResponse:
        """Which snapshot this instance serves. 503 until one is loaded."""
        snap = store.current()
        body = {
            "ok": snap is not None,
            "snapshot": snap.id if snap else None,
            "generated_at": snap.info.get("generated_at") if snap else None,
            "loaded_at": snap.loaded_at if snap else None,
            "articles": snap.info.get("articles") if snap else None,
            "source": store.source.describe(),
            "error": store.error,
        }
        return JSONResponse(body, status_code=200 if snap else 503, headers={"Cache-Control": "no-store"})

    @app.get("/v1/meta", tags=["documents"])
    def meta(request: Request) -> Response:
        """Outlets, taxonomy, model and analysis settings, `generated_at`."""
        return document(request, "meta")

    @app.get("/v1/index", tags=["documents"])
    def index(request: Request) -> Response:
        """Summary metrics of every unit (categories, topics, subtopics, events) per window."""
        return document(request, "index")

    @app.get("/v1/grid", tags=["documents"])
    def grid(request: Request) -> Response:
        """Per window, outlet and topic: article count and mean lean / China stance / tone."""
        return document(request, "grid")

    @app.get("/v1/events", tags=["documents"])
    def events(request: Request) -> Response:
        """The 200 most recently updated events."""
        return document(request, "events")

    @app.get("/v1/units/{kind}/{unit_id}", tags=["documents"])
    def unit(request: Request, kind: str, unit_id: str) -> Response:
        """Metrics of one unit per window, plus its daily trend. kind: all | category | topic | subtopic | event."""
        if kind not in schema.UNIT_KINDS:
            raise HTTPException(404, f"no such unit kind: {kind}")
        return document(request, schema.unit_doc(kind, unit_id))

    @app.get("/v1/outlets/{outlet_id}", tags=["documents"])
    def outlet(request: Request, outlet_id: str) -> Response:
        """One outlet: 90-day baseline, weekly averages, topic mix."""
        return document(request, schema.outlet_doc(outlet_id))

    @app.get("/v1/articles", tags=["articles"])
    def articles(
        request: Request,
        unit: str = Query("all/all", description="kind/id, e.g. topic/recall or event/<id>"),
        outlet: str | None = Query(None, description="Only this outlet"),
        limit: int = Query(40, ge=1, le=100),
        cursor: str | None = Query(None, description="`next` from the previous page"),
    ) -> Response:
        """Scored articles of a unit, newest first, a page at a time. The first page (no cursor) also gives
        `total` and the count per outlet for the whole unit, ignoring `outlet`."""
        kind, _, uid = unit.partition("/")
        if kind not in schema.UNIT_KINDS or (kind != "all" and not uid):
            raise HTTPException(400, "unit must be kind/id, e.g. topic/recall")
        where: list[str] = []
        args: list = []
        if kind != "all":
            where.append(f"{schema.UNIT_COLUMN[kind]} = ?")
            args.append(uid)
        unit_where, unit_args = list(where), list(args)
        if outlet:
            where.append("outlet = ?")
            args.append(outlet)
        if cursor:
            where.append("(ts, id) < (?, ?)")
            args.extend(_uncursor(cursor))

        def clause(conds: list[str]) -> str:
            return "WHERE " + " AND ".join(conds) if conds else ""

        with store.reader() as (snap, con):
            etag = f'W/"{snap.id}"'
            if not_modified(request, etag):
                return Response(status_code=304, headers={"ETag": etag, "Cache-Control": CACHE})
            rows = con.execute(f"SELECT ts, id, doc FROM articles {clause(where)} ORDER BY ts DESC, id DESC LIMIT ?", [*args, limit + 1]).fetchall()
            counts = None if cursor else con.execute(f"SELECT outlet, count(*) FROM articles {clause(unit_where)} GROUP BY outlet", unit_args).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        body = '{"articles":[' + ",".join(r[2] for r in rows) + '],"next":' + json.dumps(_cursor(rows[-1][0], rows[-1][1]) if more else None)
        if counts is not None:
            body += f',"total":{sum(n for _, n in counts)},"outlets":' + json.dumps(dict(counts))
        return Response(body + "}", media_type="application/json", headers={"ETag": etag, "Cache-Control": CACHE})

    @app.post("/v1/hit", status_code=204, tags=["status"])
    async def hit(request: Request) -> Response:
        """Count one anonymous page view (see api/hits.py). Sent by the website; nothing is returned."""
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > hits.MAX_BODY:
                break
        hits.record(body, _client_ip(request), request.headers.get("user-agent", ""))
        return Response(status_code=204, headers={"Cache-Control": "no-store"})

    return app


app = create_app()
