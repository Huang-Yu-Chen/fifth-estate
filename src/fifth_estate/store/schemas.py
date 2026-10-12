"""Arrow schemas for every lake table (keeps column types stable across runs)."""

import pyarrow as pa

TS = pa.timestamp("us", tz="UTC")
STR = pa.string()
I32 = pa.int32()
F32 = pa.float32()
F64 = pa.float64()
BOOL = pa.bool_()
STRS = pa.list_(pa.string())

_USAGE = [
    ("model", STR),
    ("reasoning_effort", STR),
    ("service_tier", STR),
    ("prompt_hash", STR),
    ("clean_version", STR),
    ("input_tokens", I32),
    ("cached_tokens", I32),
    ("cache_write_tokens", I32),
    ("output_tokens", I32),
    ("reasoning_tokens", I32),
    ("cost_usd", F64),
    ("status", STR),  # ok | error | refused
    ("error", STR),
    ("created_at", TS),
    ("run_id", STR),
]

SCHEMAS: dict[str, pa.Schema] = {
    # Every URL we tried (or decided not) to fetch. Used for dedupe and scraper health.
    "fetches": pa.schema(
        [
            ("run_id", STR),
            ("outlet_id", STR),
            ("url", STR),
            ("canonical_url", STR),
            ("source", STR),
            ("status", STR),  # ok | skip_url | skip_section | robots | http_error | extract_failed | too_short | not_provider
            ("http_status", I32),
            ("section", STR),
            ("attempt", I32),
            ("error", STR),
            ("title_hint", STR),
            ("published_hint", TS),
            ("created_at", TS),
        ]
    ),
    "articles": pa.schema(
        [
            ("article_id", STR),
            ("outlet_id", STR),
            ("url", STR),
            ("canonical_url", STR),
            ("source", STR),
            ("via", STR),
            ("provider_id", STR),
            ("discovered_at", TS),
            ("fetched_at", TS),
            ("published_at", TS),
            ("title", STR),
            ("section", STR),
            ("body", STR),  # extracted text (not yet cleaned; cleaning is versioned and applied downstream)
            ("n_chars", I32),
            ("method", STR),
            ("byline", STRS),
            ("keywords", STRS),
            ("content_hash", STR),
            ("run_id", STR),
            ("created_at", TS),
        ]
    ),
    # Post-hoc flags (wire copies etc.). Latest row per article wins.
    "article_flags": pa.schema(
        [
            ("article_id", STR),
            ("is_wire_copy", BOOL),
            ("wire_of", STR),
            ("wire_similarity", F32),
            ("created_at", TS),
            ("run_id", STR),
        ]
    ),
    "stage1": pa.schema(
        [
            ("article_id", STR),
            ("scope_reason", STR),
            ("scope", STR),
            ("takeaways", STRS),
            ("topic_id", STR),
            ("subtopic_id", STR),
            ("proposed_subtopic", STR),
            ("proposed_subtopic_desc", STR),
            ("news_type", STR),
            ("news_type_reason", STR),
            *_USAGE,
        ]
    ),
    "stage2": pa.schema(
        [
            ("article_id", STR),
            ("batch_id", STR),
            ("tone_reason", STR),
            ("tone", I32),
            ("title_tone_reason", STR),
            ("title_tone", I32),
            ("lean_reason", STR),
            ("lean", I32),
            ("title_lean_reason", STR),
            ("title_lean", I32),
            ("china_relevance", I32),
            ("china_reason", STR),
            ("china_stance", I32),
            ("title_china_reason", STR),
            ("title_china_stance", I32),
            *_USAGE,
        ]
    ),
    # Subtopic re-assignment after the registry changes (latest row per article wins over stage1).
    "subtopic_assignments": pa.schema(
        [
            ("article_id", STR),
            ("topic_id", STR),
            ("subtopic_id", STR),
            *_USAGE,
        ]
    ),
    "embeddings": pa.schema(
        [
            ("article_id", STR),
            ("model", STR),
            ("dims", I32),
            ("text_hash", STR),
            ("vector_f16", pa.binary()),
            ("input_tokens", I32),
            ("cost_usd", F64),
            ("created_at", TS),
            ("run_id", STR),
        ]
    ),
    # One row per (event, day): events are recomputed daily over a sliding window.
    "events": pa.schema(
        [
            ("event_id", STR),
            ("window_end", pa.date32()),
            ("title", STR),
            ("short_title", STR),
            ("topic_id", STR),
            ("subtopic_id", STR),
            ("n_articles", I32),
            ("n_outlets", I32),
            ("first_published", TS),
            ("last_published", TS),
            ("created_at", TS),
            ("run_id", STR),
        ]
    ),
    "event_members": pa.schema(
        [
            ("event_id", STR),
            ("window_end", pa.date32()),
            ("article_id", STR),
            ("created_at", TS),
            ("run_id", STR),
        ]
    ),
    # LLM spend ledger (for the daily cost cap).
    "llm_usage": pa.schema(
        [
            ("task", STR),
            ("model", STR),
            ("service_tier", STR),
            ("n_requests", I32),
            ("input_tokens", I32),
            ("cached_tokens", I32),
            ("output_tokens", I32),
            ("cost_usd", F64),
            ("created_at", TS),
            ("run_id", STR),
        ]
    ),
}
