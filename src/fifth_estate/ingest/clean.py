"""Text cleaning and source blinding before anything is sent to a model.

Following MBD's preprocessing (drop ads, publisher self-references and boilerplate), plus
Taiwan-specific bylines and credits. The dictionary grows over time, so the version is
recorded with every annotation: bump CLEAN_VERSION whenever the rules change.
"""

from __future__ import annotations

import re
from functools import lru_cache

from ..config import load_yaml

CLEAN_VERSION = "c3"
MAX_BODY_CHARS = 6000
SHORT_LINE = 120

# Line-start bylines: 〔記者XXX／台北報導〕, 記者XXX／台北報導, 記者 XXX / 攝影 XXX 報導, 中天新聞網／綜合報導
_LEAD_BYLINES = [
    re.compile(r"^〔[^〕]{0,40}〕"),
    re.compile(r"^[（(][^（）()]{0,40}?(記者|綜合|編譯|特派|外電)[^（）()]{0,40}[）)]"),
    re.compile(r"^(實習)?(記者|特派記者|編譯|撰稿|文字|攝影|文|圖)[ 　]*[^\s／/]{1,12}([、,][^\s／/]{1,12})*[ 　]*[／/][^\n。]{0,20}?(報導|攝影|撰稿)[ 　]*"),
    re.compile(r"^記者\s*\S{2,4}\s*/\s*攝影\s*\S{2,4}\s*(報導)?"),
    re.compile(r"^\S{0,12}(新聞網|新聞|電子報|日報|時報)\s*[／/]\s*\S{0,10}報導\s*"),
    re.compile(r"^\S{0,8}(中心|小組|組|部)\s*[／/]\s*\S{0,10}報導\s*"),
]
# Everything after one of these lines is related-article links, not the article.
_END_MARKERS = re.compile(r"^(更多.{0,10}(獨家|內幕|報導|新聞|文章)[：:]?|延伸閱讀|相關新聞|相關報導|推薦閱讀|看更多|你可能也想看)[：:]?\s*$")
_ANYWHERE = [
    # photo / video credits: （外交部提供）（路透社）（圖／翻攝）（資料照）
    re.compile(r"[（(][^（）()]{0,30}(提供|攝影?|翻攝|照片|資料照|示意圖|截圖|圖片來源|圖／|圖/)[^（）()]{0,10}[）)]"),
    # editor tags at the end: （編譯：陳正健）1151012
    re.compile(r"[（(](編輯|編譯|核稿|責任編輯|校對|審校)[：:][^（）()]{0,20}[）)]\s*\d{0,8}"),
    re.compile(r"\S{0,6}(記者|攝影)\S{2,8}?(攝|傳真)\s*\d{2,3}年\d{1,2}月\d{1,2}日"),
]
_CAPTION = re.compile(r"^[▲▼△▽◎]|^[（(]?(圖|照片|影片|圖文|示意圖|翻攝)[^）)]{0,4}[／/：:]")
_SENT_SPLIT = re.compile(r"(?<=[。！？!?])")
_TITLE_PREFIX = re.compile(r"^(影音|影|快訊|獨家|直播|最新|即時|新聞|更新|LIVE)\s*[／/|｜:：]\s*")


@lru_cache
def _boilerplate() -> list[re.Pattern]:
    return [re.compile(p) for p in load_yaml("boilerplate.yaml").get("line_patterns", [])]


def clean_title(title: str | None) -> str:
    t = " ".join((title or "").split())
    prev = None
    while prev != t:
        prev, t = t, _TITLE_PREFIX.sub("", t)
    return t


def _is_boilerplate(s: str) -> bool:
    return any(p.search(s) for p in _boilerplate())


def _strip_lead(s: str) -> str:
    prev = None
    while prev != s:
        prev = s
        for p in _LEAD_BYLINES:
            s = p.sub("", s).strip()
    return s


def clean_body(body: str, self_names: tuple[str, ...] = ()) -> str:
    text = body or ""
    names = sorted((n for n in self_names if len(n) >= 2), key=len, reverse=True)
    # Parenthetical self-credits anywhere: （中央社記者XXX台北12日電）（中央社檔案照片）
    for name in names:
        text = re.sub(rf"[（(]{re.escape(name)}[^（）()]{{0,40}}[）)]", "", text)
    for p in _ANYWHERE:
        text = p.sub("", text)

    out: list[str] = []
    for raw in text.replace("\r", "").split("\n"):
        line = raw.strip()
        if _END_MARKERS.match(line):
            break
        if not line or _CAPTION.search(line):
            continue
        line = _strip_lead(line)
        if not line:
            continue
        if len(line) <= SHORT_LINE:
            if not _is_boilerplate(line):
                out.append(line)
            continue
        # Long line (e.g. JSON-LD bodies without paragraph breaks): judge sentence by sentence.
        sents = [s for s in _SENT_SPLIT.split(line) if s.strip()]
        kept = [s for s in sents if not _is_boilerplate(s)]
        if kept:
            out.append("".join(kept).strip())
    text = "\n".join(out)

    # Remaining publisher self-references (MBD) -> neutral placeholder; other outlets' names stay.
    for name in names:
        text = text.replace(name, "本媒體" if len(name) > 2 else "")
    text = re.sub(r"(本媒體){2,}", "本媒體", text)
    if len(text) > MAX_BODY_CHARS:
        text = text[:MAX_BODY_CHARS] + "……（後略）"
    return text
