"""Where the labeling site keeps its files: a private Cloud Storage bucket in production, a local
folder for development and tests. The layout is the same in both:

    labelers.json                who may label (SHA-256 of each token, never the token)
    sets/{set}.json              a label set's blinded bundle (bundle.py), written by the CLI
    labels/{set}/{labeler}.json  one labeler's labels for one set, by item key, written by the site

Every object has a version (GCS generation number, local mtime). Writes can be made conditional on
the version that was read, so two writers never silently overwrite each other.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Protocol

MISSING = 0  # version of an object that does not exist (GCS: if_generation_match=0)


class Conflict(Exception):
    """The object changed since it was read."""


class Store(Protocol):
    url: str

    def read(self, name: str) -> tuple[bytes, int] | None: ...

    def write(self, name: str, data: bytes, if_version: int | None = None) -> int: ...

    def list(self, prefix: str = "") -> dict[str, int]:
        """name → version, for every object whose name starts with `prefix`."""
        ...


class LocalStore:
    _lock = threading.Lock()

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.url = str(self.root)

    def _path(self, name: str) -> Path:
        p = (self.root / name).resolve()
        if not p.is_relative_to(self.root):
            raise ValueError(f"outside the store: {name}")
        return p

    def read(self, name: str) -> tuple[bytes, int] | None:
        p = self._path(name)
        with self._lock:
            try:
                return p.read_bytes(), p.stat().st_mtime_ns
            except FileNotFoundError:
                return None

    def write(self, name: str, data: bytes, if_version: int | None = None) -> int:
        p = self._path(name)
        with self._lock:
            if if_version is not None and (p.stat().st_mtime_ns if p.exists() else MISSING) != if_version:
                raise Conflict(name)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(f".{p.name}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, p)
            return p.stat().st_mtime_ns

    def list(self, prefix: str = "") -> dict[str, int]:
        if not self.root.exists():
            return {}
        out = {}
        for p in self.root.rglob("*"):
            name = p.relative_to(self.root).as_posix()
            if p.is_file() and not p.name.startswith(".") and name.startswith(prefix):
                out[name] = p.stat().st_mtime_ns
        return dict(sorted(out.items()))


class GCSStore:
    def __init__(self, url: str):
        from google.api_core import exceptions
        from google.cloud import storage

        bucket, _, prefix = url.removeprefix("gs://").partition("/")
        self.url = url
        self._prefix = f"{prefix.strip('/')}/" if prefix.strip("/") else ""
        # project=None: object reads and writes need no project, so none has to be configured locally.
        self._client = storage.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT"), credentials=_credentials())
        self._bucket = self._client.bucket(bucket)
        self._errors = exceptions

    def read(self, name: str) -> tuple[bytes, int] | None:
        blob = self._bucket.blob(self._prefix + name)
        try:
            data = blob.download_as_bytes()
        except self._errors.NotFound:
            return None
        return data, int(blob.generation)

    def write(self, name: str, data: bytes, if_version: int | None = None) -> int:
        blob = self._bucket.blob(self._prefix + name)
        try:
            blob.upload_from_string(data, content_type="application/json; charset=utf-8", if_generation_match=if_version)
        except self._errors.PreconditionFailed as e:
            raise Conflict(name) from e
        return int(blob.generation)

    def list(self, prefix: str = "") -> dict[str, int]:
        n = len(self._prefix)
        blobs = self._client.list_blobs(self._bucket, prefix=self._prefix + prefix)
        return dict(sorted((b.name[n:], int(b.generation)) for b in blobs))


def _credentials():
    """Cloud Run: the service account. Laptop: application-default credentials if set up, otherwise
    the `gcloud auth login` account's access token (valid for an hour, enough for one command)."""
    import google.auth
    from google.auth.exceptions import DefaultCredentialsError
    from google.oauth2.credentials import Credentials

    try:
        return google.auth.default(scopes=["https://www.googleapis.com/auth/devstorage.read_write"])[0]
    except DefaultCredentialsError:
        pass
    try:
        token = subprocess.run(["gcloud", "auth", "print-access-token"], check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as e:
        raise SystemExit("No Google Cloud credentials: run `gcloud auth login` first.") from e
    return Credentials(token)


def open_store(url: str | None = None) -> Store:
    url = url or os.environ.get("FIFTH_LABEL_STORE")
    if not url:
        raise SystemExit("No labeling store: set FIFTH_LABEL_STORE (gs://bucket or a local folder) or pass --store.")
    return GCSStore(url) if url.startswith("gs://") else LocalStore(url)


def load(store: Store, name: str, default: Any = None) -> Any:
    got = store.read(name)
    return json.loads(got[0]) if got else default


def save(store: Store, name: str, obj: Any, if_version: int | None = None, compact: bool = False) -> int:
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) if compact else json.dumps(obj, ensure_ascii=False, indent=1)
    return store.write(name, text.encode("utf-8"), if_version)


def update(store: Store, name: str, change: Callable[[Any], Any], default: Any, compact: bool = False) -> tuple[Any, int]:
    """Read, change and write back; starts over if someone else wrote in between.
    Returns the written object and its new version."""
    for attempt in range(8):
        got = store.read(name)
        obj = change(json.loads(got[0]) if got else copy.deepcopy(default))
        try:
            return obj, save(store, name, obj, if_version=got[1] if got else MISSING, compact=compact)
        except Conflict:
            time.sleep(0.05 * (attempt + 1))
    raise Conflict(name)
