"""The label form: the same fields as the local `fifth label` page. `fifth eval` compares them with
Stage 1 (scope, news_type) and Stage 2 (lean, china_relevance, china_stance, tone)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Stage 1's three out-of-scope categories (中國內政, 與台灣無關的國際新聞, 非政治) are one choice here.
SCOPES = ("國內政治", "外交", "兩岸", "不在範圍")
NEWS_TYPES = ("新聞報導", "新聞分析", "評論")
Score = Literal[-5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5]
REQUIRED = ("scope", "lean", "china_relevance", "tone", "news_type")
SCORED = (*REQUIRED, "china")


class Label(BaseModel):
    """One labeler's answer for one article. A skipped article has no scores."""

    model_config = ConfigDict(extra="forbid")

    skipped: bool = False
    skip_reason: str = Field("", max_length=500)
    scope: Literal[SCOPES] | None = None  # type: ignore[valid-type]
    lean: Score | None = None
    china_relevance: Literal[0, 1, 2] | None = None
    china: Score | None = None  # 抗中友中; empty when the article has nothing to do with China
    tone: Score | None = None
    news_type: Literal[NEWS_TYPES] | None = None  # type: ignore[valid-type]
    note: str = Field("", max_length=2000)
    seconds: int | None = Field(None, ge=0, le=86_400)  # time the article was on screen

    @model_validator(mode="after")
    def _complete(self) -> Label:
        if self.skipped:
            for f in SCORED:
                setattr(self, f, None)
            return self
        missing = [f for f in REQUIRED if getattr(self, f) is None]
        if self.china_relevance and self.china is None:
            missing.append("china")
        if missing:
            raise ValueError("missing: " + ", ".join(missing))
        if self.china_relevance == 0 and self.china is not None:
            raise ValueError("china must be empty when china_relevance is 0")
        return self
