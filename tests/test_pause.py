"""When the OpenAI quota runs out: quota errors are recognized and never retried, no article loses an
attempt, and the state goes paused → catching_up → ok."""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import httpx2
import pytest
from openai import AuthenticationError, BadRequestError, RateLimitError

from fifth_estate.annotate import pause, stage1, stage2
from fifth_estate.cluster import embed, events
from fifth_estate.llm import batch
from fifth_estate.llm import client as llm
from fifth_estate.store import lake, state


def _err(cls, status, code=None, type_=None):
    req = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    return cls(f"Error code: {status}", response=httpx2.Response(status, request=req), body={"message": "m", "code": code, "type": type_})


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("FIFTH_DATA_DIR", str(tmp_path))
    return tmp_path


# --- recognizing quota errors ---------------------------------------------------------------


@pytest.mark.parametrize(
    "err, quota",
    [
        (_err(RateLimitError, 429, "credit_balance_exhausted"), True),
        (_err(RateLimitError, 429, "project_spend_limit_exceeded"), True),
        (_err(RateLimitError, 429, None, "insufficient_quota"), True),
        (_err(BadRequestError, 400, "billing_hard_limit_reached"), True),
        (_err(RateLimitError, 429, "rate_limit_exceeded", "requests"), False),
        (_err(RateLimitError, 429, "slow_down", "rate_limit_error"), False),
        ({"error": {"code": "insufficient_quota", "message": "m"}}, True),  # Batch output line body
        ({"code": "organization_spend_limit_exceeded", "message": "m"}, True),  # Batch error file line
        ({"error": {"code": "server_error"}}, False),
        (None, False),
    ],
)
def test_is_quota_error(err, quota):
    assert llm.is_quota_error(err) is quota


class _Responses:
    def __init__(self, err):
        self.err, self.calls = err, 0

    async def create(self, **kw):
        self.calls += 1
        raise self.err


def test_quota_error_is_not_retried():
    c = SimpleNamespace(responses=_Responses(_err(RateLimitError, 429, "credit_balance_exhausted")))
    with pytest.raises(llm.QuotaExhausted):
        asyncio.run(llm.call_async(c, {}, None, "flex"))
    assert c.responses.calls == 1


def test_rate_limit_is_still_retried(monkeypatch):
    async def no_sleep(_):
        pass

    monkeypatch.setattr(llm.asyncio, "sleep", no_sleep)
    c = SimpleNamespace(responses=_Responses(_err(RateLimitError, 429, "rate_limit_exceeded")))
    res = asyncio.run(llm.call_async(c, {}, None, "flex", attempts=3))
    assert res.status == "error" and c.responses.calls == 3


# --- pause state ----------------------------------------------------------------------------


def test_pause_resume_catch_up(data, monkeypatch):
    assert not pause.site_paused() and pause.lookback_days() == 7
    since = pause.pause("credit_balance_exhausted")["since"]
    assert pause.site_paused() and pause.load()["status"] == "paused"
    pause.still_paused("still out")
    pause.resume()
    assert pause.site_paused() and pause.load()["status"] == "catching_up"
    pause.pause("out again")
    assert pause.load()["since"] == since  # the look-back must still reach the first outage
    pause.resume()
    monkeypatch.setattr(pause, "backlog", lambda days, before: {"stage1": 3, "stage2": 0, "embed": 0})
    assert pause.finish_run(7)["status"] == "catching_up" and pause.site_paused()
    monkeypatch.setattr(pause, "backlog", lambda days, before: {"stage1": 0, "stage2": 0, "embed": 0})
    st = pause.finish_run(7)
    assert st["status"] == "ok" and st["last_pause"]["since"] == since
    assert not pause.site_paused() and pause.lookback_days() == 7


def test_annotate_while_paused(data, monkeypatch):
    from fifth_estate import cli

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9/v1")  # nothing here may reach OpenAI
    pause.pause("credit_balance_exhausted")
    monkeypatch.setattr(llm, "check_quota", lambda: _err(RateLimitError, 429, "credit_balance_exhausted"))
    cli.annotate(summary=None)  # still used up: a quiet no-op
    assert pause.load()["status"] == "paused"
    monkeypatch.setattr(llm, "check_quota", lambda: _err(AuthenticationError, 401, "invalid_api_key"))
    with pytest.raises(SystemExit):  # anything else needs a person: the run fails, the pause stays
        cli.annotate(summary=None)
    assert pause.load()["status"] == "paused"
    monkeypatch.setattr(llm, "check_quota", lambda: None)
    cli.annotate(summary=None)  # back, and an empty lake has no backlog: straight to ok
    assert pause.load()["status"] == "ok"


def test_lookback_reaches_back_to_the_outage(data):
    state.save(pause.STATE, {"status": "paused", "since": (lake.now_utc() - timedelta(days=10)).isoformat()})
    assert pause.lookback_days() == 17
    state.save(pause.STATE, {"status": "catching_up", "since": (lake.now_utc() - timedelta(days=400)).isoformat()})
    assert pause.lookback_days() == 95  # not further back than the website's longest window


# --- Batch: requests that ran into the quota are resubmitted, not recorded as failures -------------


def _line(cid, status, body):
    return json.dumps({"custom_id": cid, "response": {"status_code": status, "body": body}})


def test_quota_lines_in_a_finished_batch_are_not_recorded(data, monkeypatch):
    labels = {"tone_reason": "r", "tone": 0, "title_tone_reason": "r", "title_tone": 0, "lean_reason": "r", "lean": 1,
              "title_lean_reason": "r", "title_lean": 0, "china_relevance": 0, "china_reason": "r", "china_stance": None,
              "title_china_reason": "r", "title_china_stance": None}
    ok = {"status": "completed", "usage": {"input_tokens": 10, "output_tokens": 5},
          "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(labels)}]}]}
    files = {
        "out": "\n".join([
            _line("stage2:a", 429, {"error": {"code": "credit_balance_exhausted", "type": "insufficient_quota", "message": "m"}}),
            _line("stage2:b", 200, ok),
        ]),
        "err": json.dumps({"custom_id": "stage2:c", "response": None, "error": {"code": "insufficient_quota", "message": "m"}}),
    }
    client = SimpleNamespace(
        batches=SimpleNamespace(retrieve=lambda bid: SimpleNamespace(id=bid, status="completed", output_file_id="out", error_file_id="err", request_counts=None)),
        files=SimpleNamespace(content=lambda fid: SimpleNamespace(text=files[fid])),
    )
    monkeypatch.setattr(llm, "sync_client", lambda: client)
    state.save(batch.STATE, [{"id": "b1", "task": "stage2", "status": "submitted", "custom_ids": ["stage2:a", "stage2:b", "stage2:c"],
                              "model": "gpt-6-luna", "reasoning_effort": "medium", "prompt_hash": "h"}])
    rep = stage2.collect(lake.new_run_id())
    assert rep["collected"] == 1 and rep["ok"] == 1 and rep["quota_skipped"] == 2
    assert [r["article_id"] for r in lake.rows(f"SELECT article_id FROM {lake.source('stage2')}")] == ["b"]
    assert batch.pending_ids("stage2") == set()  # a and c are free to be submitted again


# --- backlog counts -------------------------------------------------------------------------------


def test_backlog_counts(data):
    run = lake.new_run_id()
    now = lake.now_utc()
    fetched = {"a1": now, "a2": now, "a3": now, "a4": now - timedelta(hours=3)}
    lake.write("articles", [{"article_id": a, "outlet_id": "ltn", "title": a, "body": "x", "created_at": t, "published_at": t} for a, t in fetched.items()], run)
    lake.write("stage1", [
        {"article_id": "a1", "status": "ok", "scope": "兩岸", "takeaways": ["t"], "created_at": now},
        {"article_id": "a2", "status": "error", "created_at": now},  # attempts left: still waiting
        {"article_id": "a3", "status": "ok", "scope": "非政治", "created_at": now},
        {"article_id": "a4", "status": "ok", "scope": "外交", "takeaways": ["t"], "created_at": now},
    ], run)
    assert stage1.count_pending(7) == 1
    assert stage2.count_unlabeled(7) == 2 and embed.count_pending(7) == 2
    # In a running batch: articles fetched after the quota came back are normal operation; older ones are backlog.
    assert stage2.count_unlabeled(7, exclude={"stage2:a1", "stage2:a4"}) == 0
    assert stage2.count_unlabeled(7, exclude={"stage2:a1", "stage2:a4"}, before=now - timedelta(hours=1)) == 1
    lake.write("stage2", [{"article_id": a, "status": "ok", "prompt_hash": "older-codebook", "created_at": now} for a in ("a1", "a4")], run)
    assert stage2.count_unlabeled(7) == 0  # re-labels after a codebook change are not backlog
    lake.write("embeddings", [{"article_id": a, "created_at": now} for a in ("a1", "a4")], run)
    assert embed.count_pending(7) == 0


# --- events of the days publish skipped ---------------------------------------------------------


def test_missed_event_days(data):
    today = lake.taipei_today()
    assert events.missed_days() == [today - timedelta(days=i) for i in range(15, 0, -1)]
    lake.write("events", [{"event_id": "e", "window_end": today - timedelta(days=3), "created_at": lake.now_utc()}], "r1", day=today - timedelta(days=3))
    assert events.missed_days() == [today - timedelta(days=2), today - timedelta(days=1)]
    lake.write("events", [{"event_id": "e", "window_end": today, "created_at": lake.now_utc()}], "r2", day=today)
    assert events.missed_days() == []
