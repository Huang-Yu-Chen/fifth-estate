"""OpenAI Responses API wrapper: request building, Flex with fallback, parsing, cost."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    BadRequestError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)
from openai.lib._parsing._responses import type_to_text_format_param
from pydantic import BaseModel, ValidationError

from ..config import load_yaml
from ..store import lake

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TaskConfig:
    name: str
    model: str
    reasoning_effort: str | None = None
    service_tier: str = "default"
    max_output_tokens: int | None = None
    dimensions: int | None = None


def task_config(name: str) -> TaskConfig:
    raw = load_yaml("models.yaml")["tasks"][name]
    return TaskConfig(name=name, **raw)


def require_key() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set (put it in .env locally, or as a GitHub Actions secret).")


def sync_client() -> OpenAI:
    require_key()
    return OpenAI(timeout=900, max_retries=0)


def async_client() -> AsyncOpenAI:
    require_key()
    return AsyncOpenAI(timeout=900, max_retries=0)


def _explicit_cache(model: str) -> bool:
    return model.startswith(("gpt-6", "gpt-5.6"))


def _reasoning(model: str) -> bool:
    return model.startswith(("gpt-5", "gpt-6", "o3", "o4"))


def build_body(cfg: TaskConfig, system_text: str, user_text: str, schema: type[BaseModel]) -> dict[str, Any]:
    """A /v1/responses request body. The static codebook goes first, behind an explicit cache
    breakpoint, so per-article text is not billed as a cache write (GPT-5.6+/GPT-6)."""
    sys_block: dict[str, Any] = {"type": "input_text", "text": system_text}
    body: dict[str, Any] = {
        "model": cfg.model,
        "input": [
            {"role": "developer", "content": [sys_block]},
            {"role": "user", "content": [{"type": "input_text", "text": user_text}]},
        ],
        "text": {"format": type_to_text_format_param(schema)},
        "store": False,
    }
    if cfg.max_output_tokens:
        body["max_output_tokens"] = cfg.max_output_tokens
    if _explicit_cache(cfg.model):
        sys_block["prompt_cache_breakpoint"] = {"mode": "explicit"}
        body["prompt_cache_options"] = {"mode": "explicit"}
    if cfg.reasoning_effort and _reasoning(cfg.model):
        body["reasoning"] = {"effort": cfg.reasoning_effort}
    return body


def prompt_hash(*parts: Any) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update((p if isinstance(p, str) else json.dumps(p, sort_keys=True, ensure_ascii=False)).encode())
    return h.hexdigest()[:12]


# --------------------------------------------------------------------------- results


@dataclass
class Result:
    parsed: BaseModel | None
    status: str  # ok | error | refused | incomplete
    error: str | None
    usage: dict[str, int]
    service_tier: str
    latency: float | None = None


def _usage(u: Any) -> dict[str, int]:
    if u is None:
        return {}
    d = u if isinstance(u, dict) else u.model_dump()
    itd = d.get("input_tokens_details") or {}
    otd = d.get("output_tokens_details") or {}
    return {
        "input_tokens": int(d.get("input_tokens") or 0),
        "cached_tokens": int(itd.get("cached_tokens") or 0),
        "cache_write_tokens": int(itd.get("cache_write_tokens") or 0),
        "output_tokens": int(d.get("output_tokens") or 0),
        "reasoning_tokens": int(otd.get("reasoning_tokens") or 0),
    }


def parse_response(resp: Any, schema: type[BaseModel], tier: str) -> Result:
    """Works for SDK Response objects and raw dicts from Batch output files."""
    d = resp if isinstance(resp, dict) else resp.model_dump()
    usage = _usage(d.get("usage"))
    texts, refusal = [], None
    for item in d.get("output") or []:
        if item.get("type") != "message":
            continue
        for c in item.get("content") or []:
            if c.get("type") == "output_text":
                texts.append(c.get("text") or "")
            elif c.get("type") == "refusal":
                refusal = c.get("refusal")
    if refusal:
        return Result(None, "refused", refusal[:500], usage, tier)
    if d.get("status") == "incomplete":
        reason = (d.get("incomplete_details") or {}).get("reason")
        return Result(None, "incomplete", f"incomplete: {reason}", usage, tier)
    try:
        return Result(schema.model_validate(json.loads("".join(texts))), "ok", None, usage, tier)
    except (json.JSONDecodeError, ValidationError) as e:
        return Result(None, "error", f"{type(e).__name__}: {e}"[:500], usage, tier)


def cost_usd(model: str, usage: dict[str, int], tier: str) -> float:
    p = load_yaml("models.yaml")["pricing"].get(model)
    if not p or not usage:
        return 0.0
    mult = {"flex": 0.5, "batch": 0.5, "fast": 2.0, "priority": 2.0}.get(tier, 1.0)
    cached = usage.get("cached_tokens", 0)
    write = usage.get("cache_write_tokens", 0)
    fresh = max(usage.get("input_tokens", 0) - cached - write, 0)
    total = (
        fresh * p["input"]
        + cached * p.get("cached", p["input"])
        + write * p.get("cache_write", p["input"])
        + usage.get("output_tokens", 0) * p.get("output", 0)
    )
    return total / 1e6 * mult


# --------------------------------------------------------------------------- calls


async def call_async(
    client: AsyncOpenAI, body: dict[str, Any], schema: type[BaseModel], tier: str, attempts: int = 5
) -> Result:
    """One request. Flex 429s ("resource unavailable") are retried, then fall back to default."""
    delay = 5.0
    last = "unknown error"
    t0 = time.monotonic()
    for i in range(attempts):
        use_tier = tier if not (tier == "flex" and i >= 3) else "default"
        try:
            resp = await client.responses.create(**body, service_tier=use_tier)
            res = parse_response(resp, schema, use_tier)
            res.latency = time.monotonic() - t0
            return res
        except RateLimitError as e:
            last = f"RateLimitError: {e}"
        except (APITimeoutError, APIConnectionError, InternalServerError) as e:
            last = f"{type(e).__name__}: {e}"
        except BadRequestError as e:
            return Result(None, "error", f"BadRequestError: {e}"[:500], {}, use_tier)
        await asyncio.sleep(delay)
        delay *= 2
    return Result(None, "error", last[:500], {}, tier)


def spent_today() -> float:
    today = lake.taipei_today().isoformat()
    r = lake.rows(f"SELECT coalesce(sum(cost_usd), 0) AS c FROM {lake.source('llm_usage', 1)} WHERE CAST(date AS VARCHAR) = ?", [today])
    return float(r[0]["c"]) if r else 0.0


def daily_budget() -> float:
    return float(load_yaml("models.yaml")["budget"]["daily_usd"])


def record_usage(task: str, model: str, tier: str, results: list[Result], run_id: str) -> float:
    total = sum(cost_usd(model, r.usage, r.service_tier) for r in results)
    lake.write(
        "llm_usage",
        [
            {
                "task": task,
                "model": model,
                "service_tier": tier,
                "n_requests": len(results),
                "input_tokens": sum(r.usage.get("input_tokens", 0) for r in results),
                "cached_tokens": sum(r.usage.get("cached_tokens", 0) for r in results),
                "output_tokens": sum(r.usage.get("output_tokens", 0) for r in results),
                "cost_usd": total,
                "created_at": lake.now_utc(),
                "run_id": run_id,
            }
        ],
        run_id,
    )
    return total
