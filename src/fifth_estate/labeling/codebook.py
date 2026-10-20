"""The codebook human labelers read: the model's own prompts (prompts/stage1.md, stage2.md) cut into
the sections for the fields on the form, plus the background brief. Humans and the model work from
the same text; only the model-specific instructions are explained away in NOTE."""

from __future__ import annotations

import hashlib
import json
import re

from ..config import CONFIG_DIR, PROMPTS_DIR

NOTE = (
    "這是 AI 模型使用的同一份編碼手冊。手冊中的「標題另外評一次」以及 title_…、…_reason 等欄位只適用於模型，"
    "你只需要評整篇文章。範圍的三個非政治類別（中國內政、與台灣無關的國際新聞、非政治）在表單上合併為「不在範圍」。"
)

# form field → prompt holding its section (the field name is in the section heading: ## 2. 政黨傾向（lean）)
PARTS = (("scope", "stage1"), ("lean", "stage2"), ("china", "stage2"), ("tone", "stage2"), ("news_type", "stage1"))

_HEADING = re.compile(r"^##\s+(?:\d+\.\s*)?(.+?)(?:（([a-z_]+)）)?\s*$")


def _split(md: str) -> dict[str, tuple[str, list[str]]]:
    """'## 2. 政黨傾向（lean）' and the lines under it → {'lean': ('政黨傾向', lines)}"""
    out: dict[str, tuple[str, list[str]]] = {}
    cur = None
    for line in md.splitlines():
        if m := _HEADING.match(line):
            cur = m.group(2) or m.group(1)
            out[cur] = (m.group(1), [])
        elif cur:
            out[cur][1].append(line)
    return out


def blocks(lines: list[str]) -> list[dict]:
    """Paragraphs and bullet lists, the only Markdown the prompts use."""
    out: list[dict] = []
    in_list = False
    for line in lines:
        s = line.strip()
        if s.startswith("- "):
            if in_list:
                out[-1]["items"].append(s[2:].strip())
            else:
                out.append({"type": "ul", "items": [s[2:].strip()]})
            in_list = True
        else:
            if s:
                out.append({"type": "p", "text": s})
            in_list = False
    return out


def sections() -> list[dict]:
    prompts = {name: _split((PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")) for name in ("stage1", "stage2")}
    out = []
    for field, prompt in PARTS:
        title, lines = prompts[prompt][field]
        out.append({"id": field, "title": title, "blocks": blocks(lines)})
    brief = (CONFIG_DIR / "context_brief.md").read_text(encoding="utf-8")
    out.append({"id": "background", "title": "背景資料", "blocks": blocks(brief.splitlines())})
    return out


def version(secs: list[dict]) -> str:
    """Short hash of what labelers read; recorded with every label."""
    return hashlib.sha256(json.dumps([NOTE, secs], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]
