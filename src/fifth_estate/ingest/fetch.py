"""Polite async HTTP: per-domain spacing, robots.txt, retries."""

from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from ..config import IngestSettings

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}


class Fetcher:
    def __init__(self, settings: IngestSettings):
        self.settings = settings
        self.client = httpx.AsyncClient(
            http2=True,
            follow_redirects=True,
            timeout=settings.request_timeout_sec,
            headers={
                "User-Agent": settings.user_agent,
                "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.5",
            },
        )
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}

    async def __aenter__(self) -> "Fetcher":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.client.aclose()

    def _lock(self, host: str) -> asyncio.Lock:
        if host not in self._locks:
            self._locks[host] = asyncio.Lock()
        return self._locks[host]

    async def _spaced_get(self, url: str) -> httpx.Response:
        host = urlsplit(url).netloc
        async with self._lock(host):
            wait = self._last.get(host, 0) + self.settings.per_domain_interval_sec - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                return await self.client.get(url)
            finally:
                self._last[host] = time.monotonic()

    async def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        key = f"{parts.scheme}://{parts.netloc}"
        if key not in self._robots:
            rp: RobotFileParser | None = RobotFileParser()
            try:
                r = await self._spaced_get(f"{key}/robots.txt")
                if r.status_code >= 400:
                    rp = None  # no robots.txt (or unreadable): allowed
                else:
                    rp.parse(r.text.splitlines())
            except httpx.HTTPError:
                rp = None
            self._robots[key] = rp
        rp = self._robots[key]
        return True if rp is None else rp.can_fetch(self.settings.user_agent, url)

    async def get(self, url: str, attempts: int = 3) -> httpx.Response:
        """GET with retries on transient failures. Returns the last response (may be 4xx)."""
        delay = 2.0
        last_exc: Exception | None = None
        for i in range(attempts):
            try:
                r = await self._spaced_get(url)
                if r.status_code not in RETRY_STATUS:
                    return r
                last_exc = httpx.HTTPStatusError(f"status {r.status_code}", request=r.request, response=r)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_exc = e
            if i < attempts - 1:
                await asyncio.sleep(delay)
                delay *= 2
        if isinstance(last_exc, httpx.HTTPStatusError):
            return last_exc.response
        raise last_exc  # type: ignore[misc]
