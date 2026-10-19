"""Which snapshot the API serves, and how it moves to a newer one without a restart.

Sources:
  LocalFile  FIFTH_SERVING_DB (default build/serving/serving.sqlite): local development, `fifth serve`.
  GCSObject  FIFTH_SERVING_BUCKET (Cloud Run): gs://<bucket>/serving.sqlite.gz, a versioned object that every
             publish overwrites. Each generation is downloaded once, unpacked to local disk (on Cloud Run that
             is memory) and opened read-only.

The snapshot is loaded at startup. After that, a request at most every CHECK_SECONDS starts a background
check for a newer generation; requests are answered from the snapshot in hand until the new one is ready.
A replaced snapshot's file is deleted once the last request reading it has finished.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Protocol

from . import schema

CHECK_SECONDS = 60.0
RETRY_SECONDS = 10.0  # while there is nothing to serve yet


def log(severity: str, message: str, **fields) -> None:
    """One JSON line on stdout: Cloud Run turns it into a log entry with this severity."""
    print(json.dumps({"severity": severity, "message": message, **fields}, ensure_ascii=False, default=str), file=sys.stdout, flush=True)


class NoSnapshot(RuntimeError):
    """Nothing to serve (yet)."""


class Snapshot:
    def __init__(self, path: Path, version: str, owned: bool) -> None:
        self.path = path
        self.version = version
        self.owned = owned  # a downloaded copy, deleted once replaced and no longer read
        self.uri = "file:" + urllib.parse.quote(str(path)) + "?mode=ro&immutable=1"
        con = sqlite3.connect(self.uri, uri=True)
        try:
            self.info = {k: json.loads(v) for k, v in con.execute("SELECT key, value FROM info")}
        finally:
            con.close()
        if self.info.get("schema") != schema.SCHEMA_VERSION:
            raise NoSnapshot(f"snapshot schema {self.info.get('schema')!r}, this API reads {schema.SCHEMA_VERSION}")
        self.id: str = self.info["snapshot"]
        self.loaded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._lock = threading.Lock()
        self._readers = 0
        self._retired = False

    def acquire(self) -> None:
        with self._lock:
            self._readers += 1

    def release(self) -> None:
        with self._lock:
            self._readers -= 1
            gone = self._retired and self._readers == 0
        if gone:
            self._remove()

    def retire(self) -> None:
        with self._lock:
            self._retired = True
            gone = self._readers == 0
        if gone:
            self._remove()

    def _remove(self) -> None:
        if self.owned:
            self.path.unlink(missing_ok=True)


class Source(Protocol):
    def describe(self) -> str: ...
    def version(self) -> str: ...
    def fetch(self, version: str, workdir: Path) -> tuple[Path, bool]: ...


class LocalFile:
    """A snapshot file on this machine, served in place (publish replaces it atomically)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def describe(self) -> str:
        return str(self.path)

    def version(self) -> str:
        try:
            st = self.path.stat()
        except FileNotFoundError as e:
            raise NoSnapshot(f"{self.path} does not exist yet (run `fifth export` or `fifth publish`)") from e
        return f"{st.st_ino}-{st.st_mtime_ns}-{st.st_size}"

    def fetch(self, version: str, workdir: Path) -> tuple[Path, bool]:
        return self.path, False


class GCSObject:
    """A Cloud Storage object, read with the Cloud Run service account's token from the metadata server."""

    TOKEN_URL = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"

    def __init__(self, bucket: str, name: str = "serving.sqlite.gz") -> None:
        self.bucket = bucket
        self.name = name
        self._token = ""
        self._token_expires = 0.0

    def describe(self) -> str:
        return f"gs://{self.bucket}/{self.name}"

    def _headers(self) -> dict[str, str]:
        if time.time() > self._token_expires - 120:
            req = urllib.request.Request(self.TOKEN_URL, headers={"Metadata-Flavor": "Google"})
            with urllib.request.urlopen(req, timeout=10) as r:
                tok = json.load(r)
            self._token, self._token_expires = tok["access_token"], time.time() + tok["expires_in"]
        return {"Authorization": f"Bearer {self._token}"}

    def _url(self, **params: str) -> str:
        name = urllib.parse.quote(self.name, safe="")
        return f"https://storage.googleapis.com/storage/v1/b/{self.bucket}/o/{name}?{urllib.parse.urlencode(params)}"

    def version(self) -> str:
        req = urllib.request.Request(self._url(fields="generation"), headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return str(json.load(r)["generation"])
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NoSnapshot(f"{self.describe()} does not exist yet") from e
            raise

    def fetch(self, version: str, workdir: Path) -> tuple[Path, bool]:
        dest = workdir / f"serving-{version}.sqlite"
        part = dest.with_name(dest.name + ".part")
        req = urllib.request.Request(self._url(alt="media", generation=version), headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
                first = r.read(1 << 20)
                unzip = zlib.decompressobj(wbits=31) if first[:2] == b"\x1f\x8b" else None  # gzip magic
                chunk = first
                while chunk:
                    f.write(unzip.decompress(chunk) if unzip else chunk)
                    chunk = r.read(1 << 20)
                if unzip:
                    f.write(unzip.flush())
                    if not unzip.eof:
                        raise OSError(f"{self.describe()} generation {version}: download ended early")
            part.replace(dest)
        finally:
            part.unlink(missing_ok=True)
        return dest, True


class Store:
    def __init__(self, source: Source, workdir: Path | None = None, check_seconds: float = CHECK_SECONDS, clock=time.monotonic) -> None:
        self.source = source
        self.workdir = workdir or Path(tempfile.mkdtemp(prefix="fifth-serving-"))
        self.check_seconds = check_seconds
        self.clock = clock
        self.snapshot: Snapshot | None = None
        self.error: str | None = None
        self._refreshing = threading.Lock()  # one refresh at a time
        self._swap = threading.Lock()  # handing out vs. replacing the current snapshot
        self._next_check = 0.0

    @classmethod
    def from_env(cls) -> Store:
        import os

        if bucket := os.environ.get("FIFTH_SERVING_BUCKET"):
            return cls(GCSObject(bucket, os.environ.get("FIFTH_SERVING_OBJECT", "serving.sqlite.gz")))
        return cls(LocalFile(Path(os.environ.get("FIFTH_SERVING_DB", "build/serving/serving.sqlite"))))

    def refresh(self) -> bool:
        """Load the source's current version unless it is the one in hand. True if a new snapshot was loaded."""
        with self._refreshing:
            started = self.clock()
            self._next_check = started + self.check_seconds
            try:
                version = self.source.version()
                if self.snapshot is not None and version == self.snapshot.version:
                    self.error = None
                    return False
                path, owned = self.source.fetch(version, self.workdir)
                try:
                    snap = Snapshot(path, version, owned)
                except Exception:
                    if owned:
                        path.unlink(missing_ok=True)
                    raise
            except Exception as e:
                self.error = f"{type(e).__name__}: {e}"[:500]
                if self.snapshot is None:
                    self._next_check = self.clock() + RETRY_SECONDS
                log("ERROR" if self.snapshot is None else "WARNING", "snapshot refresh failed", source=self.source.describe(), error=self.error)
                return False
            with self._swap:
                old, self.snapshot, self.error = self.snapshot, snap, None
            if old is not None and old.path != snap.path:
                old.retire()
            log("INFO", "snapshot loaded", snapshot=snap.id, generated_at=snap.info.get("generated_at"),
                version=version, seconds=round(self.clock() - started, 2))
            return True

    def current(self) -> Snapshot | None:
        """The snapshot in hand. Loads one if there is none; otherwise starts a background check when due."""
        now = self.clock()
        if self.snapshot is None:
            if now >= self._next_check:
                self.refresh()
        elif now >= self._next_check and not self._refreshing.locked():
            self._next_check = now + self.check_seconds
            threading.Thread(target=self.refresh, name="snapshot-refresh", daemon=True).start()
        return self.snapshot

    @contextmanager
    def reader(self) -> Iterator[tuple[Snapshot, sqlite3.Connection]]:
        """A read-only connection to the current snapshot; its file stays until the block ends."""
        if self.current() is None:
            raise NoSnapshot(self.error or "no snapshot loaded yet")
        with self._swap:
            snap = self.snapshot
            snap.acquire()
        try:
            con = sqlite3.connect(snap.uri, uri=True, check_same_thread=False)
            try:
                yield snap, con
            finally:
                con.close()
        finally:
            snap.release()
