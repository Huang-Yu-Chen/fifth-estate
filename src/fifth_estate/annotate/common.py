"""Shared helpers for building model inputs from stored articles."""

from __future__ import annotations

from ..config import PROMPTS_DIR, CONFIG_DIR, ingest_settings
from ..ingest.clean import CLEAN_VERSION, blind, clean_body, clean_title


def render_prompt(name: str, **values: str) -> str:
    text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    values.setdefault("CONTEXT_BRIEF", (CONFIG_DIR / "context_brief.md").read_text(encoding="utf-8").strip())
    for k, v in values.items():
        text = text.replace("{" + k + "}", v)
    return text


def article_text(row: dict, with_section: bool = False) -> str:
    """Cleaned, source-blinded article text. Section names are only shown to Stage 1
    (they can identify the outlet, so stance scoring never sees them)."""
    outlet = ingest_settings().outlet(row["outlet_id"])
    parts = [f"標題：{blind(clean_title(row['title']), outlet.self_names)}"]
    if with_section and row.get("section"):
        parts.append(f"版面：{row['section']}")
    parts.append("")
    parts.append("內文：")
    parts.append(clean_body(row["body"], outlet.self_names))
    return "\n".join(parts)


__all__ = ["CLEAN_VERSION", "article_text", "render_prompt"]
