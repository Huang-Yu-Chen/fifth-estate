"""Layout of a serving snapshot, shared by the writer (export/serving.py) and the API (api/app.py).

A snapshot is one read-only SQLite file built by every publish. It holds exactly what the website shows
(titles, links, scores, short model reasons), never article text.

    info(key, value)   schema version, snapshot id, generated_at, fake_data, counts
    docs(path, gz)     precomputed JSON documents, gzip-compressed: meta, index, grid, events,
                       units/<kind>/<id>, outlets/<id>
    articles           one row per in-scope article: filter columns + the article JSON the site shows
"""

from __future__ import annotations

# Bump when the tables change in a way the API has to know about; the API refuses other versions.
SCHEMA_VERSION = 1

DDL = """
CREATE TABLE info (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE docs (path TEXT PRIMARY KEY, gz BLOB NOT NULL);
CREATE TABLE articles (
    id TEXT NOT NULL,
    outlet TEXT NOT NULL,
    ts INTEGER NOT NULL,  -- published, Unix seconds; lists are newest first, ties broken by id
    category TEXT,        -- unit membership exactly as the metrics count it (metrics.aggregate.units_for)
    topic TEXT,
    subtopic TEXT,
    event TEXT,
    doc TEXT NOT NULL     -- the article as the website shows it (JSON)
);
"""

# Created after the bulk insert. Every list query is "unit (or outlet) filter, newest first".
INDEXES = """
CREATE INDEX articles_ts ON articles (ts DESC, id DESC);
CREATE INDEX articles_outlet ON articles (outlet, ts DESC, id DESC);
CREATE INDEX articles_category ON articles (category, ts DESC, id DESC);
CREATE INDEX articles_topic ON articles (topic, ts DESC, id DESC);
CREATE INDEX articles_subtopic ON articles (subtopic, ts DESC, id DESC) WHERE subtopic IS NOT NULL;
CREATE INDEX articles_event ON articles (event, ts DESC, id DESC) WHERE event IS NOT NULL;
"""

# Unit kinds (as in the site's URLs) → the articles column that holds membership. "all" has no filter.
UNIT_KINDS = ("all", "category", "topic", "subtopic", "event")
UNIT_COLUMN = {"category": "category", "topic": "topic", "subtopic": "subtopic", "event": "event"}


def unit_doc(kind: str, uid: str) -> str:
    return f"units/{kind}/{uid}"


def outlet_doc(oid: str) -> str:
    return f"outlets/{oid}"
