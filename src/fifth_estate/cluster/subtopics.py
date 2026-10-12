"""Daily subtopic discovery: Stage 1 articles labeled 'other' are grouped by the synthesis model into
new subtopics or merged into existing ones (MBD keeps a fixed subtopic list with an 'Other' fallback;
we let the list grow and log every change for weekly human review)."""

from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict

from ..annotate.common import render_prompt
from ..config import data_dir, ingest_settings
from ..ingest.clean import blind, clean_title
from ..llm import client as llm
from ..llm.schemas import IN_SCOPE, SubtopicDiscovery
from ..store import lake
from . import taxonomy

log = logging.getLogger(__name__)
MIN_GROUP = 5          # only look at topics with at least this many 'other' articles
MIN_NEW_MEMBERS = 3
MAX_ITEMS = 120
CHANGELOG = "registry/changelog.md"


def _others(days: int) -> list[dict]:
    scopes = ", ".join(f"'{s}'" for s in IN_SCOPE)
    return lake.rows(
        f"""
        WITH s1 AS (SELECT * FROM {lake.latest('stage1', days=days + 1, where="status = 'ok'")}),
        sa AS (SELECT * FROM {lake.latest('subtopic_assignments', days=days + 1, where="status = 'ok'")}),
        a AS (
            SELECT article_id, outlet_id, title, published_at FROM {lake.source('articles', days)}
            QUALIFY row_number() OVER (PARTITION BY article_id ORDER BY created_at) = 1
        )
        SELECT a.article_id, a.outlet_id, a.title, s1.topic_id, s1.proposed_subtopic, s1.takeaways
        FROM s1 JOIN a USING (article_id) LEFT JOIN sa USING (article_id)
        WHERE s1.scope IN ({scopes}) AND s1.subtopic_id = 'other' AND sa.article_id IS NULL
        ORDER BY a.published_at DESC
        """
    )


def _user_text(tax: taxonomy.Taxonomy, topic_id: str, items: list[dict]) -> str:
    t = tax.topic(topic_id)
    lines = [f"議題：{t['id']}｜{t['name']}：{t['description']}", "", "現有子議題："]
    subs = tax.active_subtopics(topic_id)
    lines += [f"- {s.id}｜{s.name}：{s.desc}" for s in subs] or ["（無）"]
    lines += ["", "標為「其他」的報導："]
    settings = ingest_settings()
    for i, r in enumerate(items):
        title = blind(clean_title(r["title"]), settings.outlet(r["outlet_id"]).self_names)
        first = (r["takeaways"] or [""])[0]
        lines.append(f"{i}. {title}｜建議：{r['proposed_subtopic'] or '—'}｜{first}")
    return "\n".join(lines)


def _slug(s: str | None, fallback: str) -> str:
    s = re.sub(r"[^a-z0-9_]", "", (s or "").lower().replace("-", "_").replace(" ", "_"))
    return s[:40] or fallback


def run(days: int = 7, run_id: str | None = None) -> dict:
    run_id = run_id or lake.new_run_id()
    tax = taxonomy.load()
    by_topic: dict[str, list[dict]] = defaultdict(list)
    for r in _others(days):
        if tax.topic(r["topic_id"]):
            by_topic[r["topic_id"]].append(r)
    todo = {t: rs[:MAX_ITEMS] for t, rs in by_topic.items() if len(rs) >= MIN_GROUP}
    if not todo or llm.spent_today() >= llm.daily_budget():
        return {"topics_reviewed": 0}
    cfg = llm.task_config("synth")
    system = render_prompt("subtopic_discovery")
    phash = llm.prompt_hash(system, cfg.model)

    async def go():
        client = llm.async_client()
        res = await asyncio.gather(
            *(llm.call_async(client, llm.build_body(cfg, system, _user_text(tax, t, items), SubtopicDiscovery), SubtopicDiscovery, cfg.service_tier)
              for t, items in todo.items())
        )
        await client.close()
        return res

    results = asyncio.run(go())
    llm.record_usage("subtopics", cfg.model, cfg.service_tier, results, run_id)
    today = lake.taipei_today().isoformat()
    existing_ids = {s.id for s in tax.subtopics}
    assignments, log_lines, created = [], [], 0
    for (topic_id, items), res in zip(todo.items(), results):
        if res.status != "ok":
            continue
        for prop in res.parsed.proposals:
            members = [items[i] for i in prop.member_indices if 0 <= i < len(items)]
            target = None
            if prop.action == "merge_into_existing" and prop.existing_subtopic_id in {s.id for s in tax.active_subtopics(topic_id)}:
                target = prop.existing_subtopic_id
            elif prop.action == "new" and len(members) >= MIN_NEW_MEMBERS and prop.name:
                new_id = _slug(prop.new_id, f"{topic_id}_{today.replace('-', '')}")
                while new_id in existing_ids:
                    new_id += "_2"
                tax.subtopics.append(taxonomy.Subtopic(id=new_id, topic=topic_id, name=prop.name.strip(), desc=(prop.desc or "").strip(), origin="auto", created=today))
                existing_ids.add(new_id)
                target, created = new_id, created + 1
                log_lines.append(f"- {today} 新增 `{new_id}`（{topic_id}）{prop.name}：{prop.desc}（{len(members)} 篇）")
            if target:
                for m in members:
                    assignments.append({"article_id": m["article_id"], "topic_id": topic_id, "subtopic_id": target,
                                        "model": cfg.model, "reasoning_effort": cfg.reasoning_effort, "service_tier": res.service_tier,
                                        "prompt_hash": phash, "status": "ok", "created_at": lake.now_utc(), "run_id": run_id})
    if created:
        taxonomy.save_subtopics(tax.subtopics)
        path = data_dir() / CHANGELOG
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write("\n".join(log_lines) + "\n")
    lake.write("subtopic_assignments", assignments, run_id)
    return {"topics_reviewed": len(todo), "new_subtopics": created, "assigned": len(assignments)}
