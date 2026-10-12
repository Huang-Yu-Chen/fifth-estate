"""Structured-output schemas. Field order is the generation order: reasons come before scores (MBD)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, create_model

Score = Literal[-5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5]

SCOPES = ("國內政治", "外交", "兩岸", "中國內政", "與台灣無關的國際新聞", "非政治")
IN_SCOPE = ("國內政治", "外交", "兩岸")
NEWS_TYPES = ("新聞報導", "新聞分析", "評論")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def stage1_model(topic_ids: list[str], subtopic_ids: list[str]) -> type[BaseModel]:
    """Stage 1 schema with the current topic/subtopic ids as enums."""
    topics = Literal[tuple([*topic_ids, "none"])]  # type: ignore[valid-type]
    subtopics = Literal[tuple([*subtopic_ids, "other", "none"])]  # type: ignore[valid-type]
    return create_model(
        "Stage1",
        __base__=_Strict,
        scope_reason=(str, ...),
        scope=(Literal[SCOPES], ...),  # type: ignore[valid-type]
        takeaways=(list[str], ...),
        topic_id=(topics, ...),
        subtopic_id=(subtopics, ...),
        proposed_subtopic=(str | None, ...),
        proposed_subtopic_desc=(str | None, ...),
        news_type_reason=(str, ...),
        news_type=(Literal[NEWS_TYPES], ...),  # type: ignore[valid-type]
    )


class Stage2(_Strict):
    tone_reason: str
    tone: Score
    title_tone_reason: str
    title_tone: Score
    lean_reason: str
    lean: Score
    title_lean_reason: str
    title_lean: Score
    china_relevance: Literal[0, 1, 2]
    china_reason: str
    china_stance: Score | None
    title_china_reason: str
    title_china_stance: Score | None


class EventTitle(_Strict):
    title: str  # MBD: a good thematic title that best summarizes the event
    short_title: str  # MBD: 4–5 words
    off_topic_indices: list[int]  # members that do not belong to the event


class SubtopicProposal(_Strict):
    action: Literal["new", "merge_into_existing", "ignore"]
    existing_subtopic_id: str | None
    new_id: str | None  # ascii snake_case
    name: str | None
    desc: str | None
    member_indices: list[int]


class SubtopicDiscovery(_Strict):
    proposals: list[SubtopicProposal]
