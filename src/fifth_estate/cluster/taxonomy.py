"""Fixed topics (config/taxonomy.yaml) and the living subtopic registry (data repo registry/subtopics.yaml)."""

from __future__ import annotations

from dataclasses import dataclass, field

import yaml

from ..config import data_dir, load_yaml

REGISTRY = "registry/subtopics.yaml"


@dataclass
class Subtopic:
    id: str
    topic: str
    name: str
    desc: str
    status: str = "active"  # active | merged | retired
    merged_into: str | None = None
    origin: str = "seed"  # seed | auto | manual
    created: str | None = None


@dataclass
class Taxonomy:
    categories: dict[str, str]
    topics: list[dict]
    subtopics: list[Subtopic] = field(default_factory=list)

    def topic(self, topic_id: str) -> dict | None:
        return next((t for t in self.topics if t["id"] == topic_id), None)

    def active_subtopics(self, topic_id: str | None = None) -> list[Subtopic]:
        return [s for s in self.subtopics if s.status == "active" and (topic_id is None or s.topic == topic_id)]

    def resolve(self, subtopic_id: str | None) -> str | None:
        """Follow merges so old annotations map to the surviving subtopic."""
        seen = set()
        sid = subtopic_id
        while sid and sid not in seen:
            seen.add(sid)
            s = next((x for x in self.subtopics if x.id == sid), None)
            if s is None or s.status != "merged" or not s.merged_into:
                return sid
            sid = s.merged_into
        return sid

    def prompt_text(self) -> str:
        """Hierarchical list for the Stage 1 prompt."""
        lines = []
        for cat_id, cat_name in self.categories.items():
            lines.append(f"### {cat_name}")
            for t in self.topics:
                if t["category"] != cat_id:
                    continue
                lines.append(f"- {t['id']}｜{t['name']}：{t['description']}")
                for s in self.active_subtopics(t["id"]):
                    lines.append(f"    - {s.id}｜{s.name}：{s.desc}")
        return "\n".join(lines)


def load() -> Taxonomy:
    raw = load_yaml("taxonomy.yaml")
    return Taxonomy(categories=raw["categories"], topics=raw["topics"], subtopics=load_subtopics())


def load_subtopics() -> list[Subtopic]:
    path = data_dir() / REGISTRY
    if not path.exists():
        seed = load_yaml("subtopics_seed.yaml")["subtopics"]
        subs = [Subtopic(**s, origin="seed") for s in seed]
        save_subtopics(subs)
        return subs
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [Subtopic(**s) for s in raw.get("subtopics", [])]


def save_subtopics(subs: list[Subtopic]) -> None:
    path = data_dir() / REGISTRY
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"subtopics": [{k: v for k, v in s.__dict__.items() if v is not None} for s in subs]}
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")
