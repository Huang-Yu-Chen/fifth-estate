"""Paths, settings and YAML config loading."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"
PROMPTS_DIR = REPO_ROOT / "prompts"
TAIPEI = ZoneInfo("Asia/Taipei")

load_dotenv(REPO_ROOT / ".env")


def data_dir() -> Path:
    """Root of the data lake (the private fifth-estate-data repo checkout)."""
    p = Path(os.environ.get("FIFTH_DATA_DIR", REPO_ROOT.parent / "fifth-estate-data"))
    p.mkdir(parents=True, exist_ok=True)
    return p


@lru_cache
def load_yaml(name: str) -> dict:
    with open(CONFIG_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


@dataclass(frozen=True)
class Source:
    type: str
    url: str | None = None
    link_pattern: str | None = None
    provider: str | None = None
    label: str | None = None
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Outlet:
    id: str
    name: str
    short: str
    self_names: tuple[str, ...]
    sources: tuple[Source, ...]
    url_include: tuple[str, ...] = ()
    url_exclude: tuple[str, ...] = ()
    allow_sections: tuple[str, ...] = ()
    deny_sections: tuple[str, ...] = ()
    section_key: str | None = None
    date_quirk: str | None = None
    via: str | None = None


@dataclass(frozen=True)
class IngestSettings:
    user_agent: str
    per_domain_interval_sec: float
    request_timeout_sec: float
    max_fetch_attempts: int
    outlets: tuple[Outlet, ...] = field(default_factory=tuple)

    def outlet(self, outlet_id: str) -> Outlet:
        for o in self.outlets:
            if o.id == outlet_id:
                return o
        raise KeyError(outlet_id)


@lru_cache
def ingest_settings() -> IngestSettings:
    raw = load_yaml("outlets.yaml")
    outlets = []
    for o in raw["outlets"]:
        sources = tuple(
            Source(
                type=s["type"],
                url=s.get("url"),
                link_pattern=s.get("link_pattern"),
                provider=s.get("provider"),
                label=s.get("label"),
                tags=tuple(s.get("tags", ())),
            )
            for s in o["sources"]
        )
        outlets.append(
            Outlet(
                id=o["id"],
                name=o["name"],
                short=o.get("short", o["name"]),
                self_names=tuple(o.get("self_names", ())),
                sources=sources,
                url_include=tuple(o.get("url_include", ())),
                url_exclude=tuple(o.get("url_exclude", ())),
                allow_sections=tuple(o.get("allow_sections", ())),
                deny_sections=tuple(o.get("deny_sections", ())),
                section_key=o.get("section_key"),
                date_quirk=o.get("date_quirk"),
                via=o.get("via"),
            )
        )
    return IngestSettings(
        user_agent=raw["user_agent"],
        per_domain_interval_sec=float(raw.get("per_domain_interval_sec", 1.0)),
        request_timeout_sec=float(raw.get("request_timeout_sec", 20)),
        max_fetch_attempts=int(raw.get("max_fetch_attempts", 3)),
        outlets=tuple(outlets),
    )
