"""Write the serving snapshot that the API reads (layout in api/schema.py).

`fifth publish` builds build/serving/serving.sqlite and a gzip copy next to it. The publish workflow uploads
the gzip copy over gs://<bucket>/serving.sqlite.gz, a versioned object (older generations are kept for a few
days, so rolling back is copying one of them back), and every API instance switches to the new generation
within a minute. The website itself is not rebuilt or redeployed.
"""

from __future__ import annotations

import gzip
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from ..api import schema
from ..config import REPO_ROOT

SERVING_DB = REPO_ROOT / "build" / "serving" / "serving.sqlite"


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)


class SnapshotWriter:
    """Builds the database in a temporary file; `finish()` moves it and its gzip copy into place."""

    def __init__(self, path: Path, snapshot_id: str) -> None:
        self.path = path
        self.id = snapshot_id
        self.tmp = path.with_name(path.name + ".tmp")
        self.seen: set[str] = set()
        self.n_docs = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        self.tmp.unlink(missing_ok=True)
        self.con = sqlite3.connect(self.tmp)
        self.con.execute("PRAGMA journal_mode = OFF")
        self.con.execute("PRAGMA synchronous = OFF")
        self.con.executescript(schema.DDL)

    def __enter__(self) -> SnapshotWriter:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:  # never leave a half-written snapshot behind
            self.con.close()
            self.tmp.unlink(missing_ok=True)

    def doc(self, path: str, obj: Any) -> None:
        self.con.execute("INSERT INTO docs VALUES (?, ?)", (path, gzip.compress(_json(obj).encode(), compresslevel=9, mtime=0)))
        self.n_docs += 1

    def article(self, a: dict, ts: int, units: dict[str, str]) -> None:
        """`a` is the article as the site shows it; `units` maps unit kind → unit id (category, topic, ...)."""
        if a["id"] in self.seen:
            return
        self.seen.add(a["id"])
        cols = [units.get(k) for k in schema.UNIT_COLUMN]
        self.con.execute("INSERT INTO articles VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (a["id"], a["outlet"], ts, *cols, _json(a)))

    def finish(self, **info: Any) -> Path:
        info = {"schema": schema.SCHEMA_VERSION, "snapshot": self.id, "docs": self.n_docs, "articles": len(self.seen), **info}
        self.con.executemany("INSERT INTO info VALUES (?, ?)", [(k, _json(v)) for k, v in info.items()])
        self.con.executescript(schema.INDEXES)
        self.con.execute("ANALYZE")
        self.con.commit()
        self.con.close()
        self.tmp.replace(self.path)
        gz = self.path.with_name(self.path.name + ".gz")
        gz_tmp = gz.with_name(gz.name + ".tmp")
        with open(self.path, "rb") as src, open(gz_tmp, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6, mtime=0) as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
        gz_tmp.replace(gz)
        return self.path
