"""Small mutable JSON state files kept in the data repo under state/."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import data_dir


def _path(name: str) -> Path:
    p = data_dir() / "state" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load(name: str, default: Any = None) -> Any:
    p = _path(name)
    if not p.exists():
        return {} if default is None else default
    return json.loads(p.read_text(encoding="utf-8"))


def save(name: str, value: Any) -> None:
    p = _path(name)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    tmp.replace(p)
