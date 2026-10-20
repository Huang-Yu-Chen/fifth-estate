"""Labeling site: blinding, assignment (independent coders per article), login, label validation,
sync into the data repo, and pooled multi-coder agreement in `fifth eval`."""

import json
import re
from datetime import datetime, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient

from fifth_estate.annotate.common import article_text, blinded
from fifth_estate.labeling import assign, bundle, codebook, server
from fifth_estate.labeling import sync as label_sync
from fifth_estate.labeling.form import Label
from fifth_estate.labeling.store import Conflict, LocalStore, load, save, update
from fifth_estate.store import lake
from fifth_estate.validate import evaluate, sample
from fifth_estate.validate.label_app import load_labels

GOOD = {"scope": "兩岸", "lean": -2, "china_relevance": 2, "china": -3, "tone": -1, "news_type": "新聞報導", "note": "", "seconds": 42}


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("FIFTH_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path / "store")


# --- what labelers read --------------------------------------------------------------------


def test_article_text_is_the_blinded_title_and_body():
    row = {"outlet_id": "ltn", "title": "快訊／自由時報獨家：立法院三讀", "section": "政治",
           "body": "〔記者王小明／台北報導〕立法院今天三讀。\n自由時報記者追問。"}
    title, body = blinded(row)
    assert article_text(row) == f"標題：{title}\n\n內文：\n{body}"
    assert article_text(row, with_section=True) == f"標題：{title}\n版面：政治\n\n內文：\n{body}"
    assert "自由時報" not in title + body and "王小明" not in body


def _article(aid: str, title: str, body: str) -> dict:
    return {"article_id": aid, "outlet_id": "ltn", "url": f"https://news.ltn.com.tw/news/politics/{aid}", "title": title,
            "body": body, "n_chars": len(body), "created_at": datetime.now(timezone.utc)}


def test_bundle_is_blinded_and_keeps_its_keys(data):
    lake.write("articles", [
        _article("a1", "自由時報獨家：國防預算", "〔記者王小明／台北報導〕國防預算今天在立法院付委。\n" + "朝野立委各有主張。" * 3),
        _article("a2", "外交部回應", "外交部今天表示，" * 5),
    ], lake.new_run_id())
    sample.save("t", ["a1", "a2", "gone"])
    b, missing = bundle.build("t", 2)
    assert missing == ["gone"]
    assert [it["article_id"] for it in b["items"]] == ["a1", "a2"]
    shown = json.dumps([[it["title"], it["body"]] for it in b["items"]], ensure_ascii=False)
    assert "自由時報" not in shown and "王小明" not in shown and "ltn" not in shown
    assert all(re.fullmatch(r"[0-9a-f]{12}", it["key"]) for it in b["items"])
    again, _ = bundle.build("t", 2, previous=b)
    assert [it["key"] for it in again["items"]] == [it["key"] for it in b["items"]]
    bundle.mark_set("t", 2)
    assert json.loads(sample.set_path("t").read_text())["coders_per_article"] == 2


def test_codebook_is_cut_from_the_model_prompts():
    secs = {s["id"]: s for s in codebook.sections()}
    assert list(secs) == ["scope", "lean", "china", "tone", "news_type", "background"]
    text = json.dumps(secs, ensure_ascii=False)
    assert "強烈支持民進黨" in text and "{CONTEXT_BRIEF}" not in text and "{TAXONOMY}" not in text
    assert any(b["type"] == "ul" for b in secs["lean"]["blocks"])


def test_page_has_no_inline_code():
    html = (server.STATIC / "index.html").read_text(encoding="utf-8")
    js = (server.STATIC / "app.js").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)", html) and not re.search(r"\son\w+=", html)
    assert "innerHTML" not in js


# --- the form ------------------------------------------------------------------------------


def test_label_form():
    assert Label(**GOOD).china == -3
    for bad in ({"china": None}, {"china_relevance": 0}, {"lean": 6}, {"scope": None}, {"outlet": "ltn"}):
        with pytest.raises(ValueError):
            Label(**{**GOOD, **bad})
    assert Label(**{**GOOD, "china_relevance": 0, "china": None}).china is None
    skipped = Label(skipped=True, lean=3, skip_reason="內容殘缺")
    assert skipped.skipped and skipped.lean is None


# --- who gets which article ----------------------------------------------------------------


def _label_round_robin(keys: list[str], labelers: list[str], k: int) -> dict:
    labels: dict[str, dict] = {lab: {} for lab in labelers}
    busy = True
    while busy:
        busy = False
        for lab in labelers:
            if key := assign.next_key(keys, lab, "s", k, labels, {}, now=0):
                labels[lab][key] = {"skipped": False}
                busy = True
    return labels


def test_every_article_gets_two_different_labelers():
    keys = [f"k{i:02d}" for i in range(30)]
    labels = _label_round_robin(keys, ["a", "b", "c"], k=2)
    assert all(n == 2 for n in assign.done_counts(labels).values()) and len(assign.done_counts(labels)) == 30
    assert assign.needed(keys, 2, labels) == 0
    loads = sorted(len(v) for v in labels.values())
    assert sum(loads) == 60 and loads[-1] - loads[0] <= 2


def test_personal_orders_differ():
    keys = [f"k{i}" for i in range(20)]
    assert assign.personal_order(keys, "a", "s") != assign.personal_order(keys, "b", "s")
    assert sorted(assign.personal_order(keys, "a", "s")) == sorted(keys)


def test_open_articles_count_as_taken_until_the_claim_expires():
    keys = ["x", "y", "z"]
    labels = {"a": {"x": {}}}
    claims = {"x": {"b": 100.0}}  # b has x open until t = 100
    assert assign.next_key(keys, "c", "s", 2, labels, claims, now=50) in ("y", "z")
    assert assign.next_key(keys, "c", "s", 2, labels, claims, now=150) == "x"  # started articles first
    assert assign.next_key(keys, "b", "s", 2, labels, claims, now=50) == "x"  # a reload gives the same one


def test_finished_labels_come_before_open_ones():
    # a labeled x and now has y open: b completes x first, whatever b's personal order
    for keys in (["x", "y"], ["y", "x"]):
        for b in ("b", "b2", "b3", "b4"):
            assert assign.next_key(keys, b, "s", 2, {"a": {"x": {}}}, {"y": {"a": 100.0}}, now=0) == "x"


def test_skips_dont_count():
    labels = {"a": {"x": {"skipped": True}}}
    assert assign.next_key(["x"], "a", "s", 2, labels, {}, 0) is None
    assert assign.next_key(["x"], "b", "s", 2, labels, {}, 0) == "x"
    assert assign.needed(["x"], 2, labels) == 2


def test_everyone_labels_everything_when_k_is_zero():
    keys = [f"k{i}" for i in range(5)]
    labels = _label_round_robin(keys, ["a", "b", "c"], k=0)
    assert all(len(v) == 5 for v in labels.values())
    assert assign.needed(keys, 0, labels) is None


# --- storage -------------------------------------------------------------------------------


def test_writes_are_conditional(store):
    v1 = save(store, "x.json", {"n": 1})
    with pytest.raises(Conflict):
        save(store, "x.json", {"n": 2}, if_version=v1 + 1)
    with pytest.raises(Conflict):
        save(store, "x.json", {"n": 2}, if_version=0)  # 0 = must not exist yet
    obj, _ = update(store, "x.json", lambda d: {"n": d["n"] + 1}, None)
    assert obj == {"n": 2} == load(store, "x.json")
    assert list(store.list()) == ["x.json"]
    with pytest.raises(ValueError):
        store.read("../outside.json")


# --- the site ------------------------------------------------------------------------------


def _site(store, monkeypatch, n: int = 4, k: int = 2):
    monkeypatch.setattr(server, "REFRESH_SECONDS", 0)
    items = [{"key": f"key{i}", "article_id": f"art{i}", "title": f"標題{i}", "body": f"第一段{i}\n第二段{i}"} for i in range(n)]
    save(store, "sets/gold.json", {"set": "gold", "title": "金標準", "active": True, "coders_per_article": k, "codebook_version": "cb1",
                                   "codebook_note": "note", "codebook": [{"id": "lean", "title": "政黨傾向", "blocks": []}], "items": items})
    people = [{"id": x, "name": x.upper(), "token_sha256": server.token_hash(f"tok-{x}"), "active": True, "sets": None} for x in "abc"]
    save(store, "labelers.json", {"labelers": people})
    return server.create_app(store)


def _client(app, who: str) -> TestClient:
    c = TestClient(app)
    assert c.post("/api/login", json={"token": f"tok-{who}"}).status_code == 200
    return c


def test_login_and_blinding(store, monkeypatch):
    app = _site(store, monkeypatch)
    anon = TestClient(app)
    assert anon.get("/api/me").status_code == 401
    assert anon.post("/api/login", json={"token": "nope"}).status_code == 403
    a = _client(app, "a")
    assert a.get("/api/me").json()["sets"] == [{"set": "gold", "title": "金標準", "items": 4, "coders_per_article": 2,
                                               "done": 0, "skipped": 0, "needed": 8}]
    r = a.post("/api/sets/gold/next", json={})
    assert set(r.json()["item"]) == {"key", "title", "body"}
    assert "art" not in r.text  # article ids never reach the browser
    assert anon.get("/health").text == "ok"
    for path in ("/", "/api/me", "/static/app.js"):
        headers = a.get(path).headers
        assert "noindex" in headers["x-robots-tag"] and headers["cache-control"] == "no-store"
        assert "default-src 'none'" in headers["content-security-policy"]


def test_two_independent_labels_per_article(store, monkeypatch):
    app = _site(store, monkeypatch, n=3)
    a, b, c = (_client(app, x) for x in "abc")
    first = a.post("/api/sets/gold/next", json={}).json()["item"]["key"]
    assert a.put(f"/api/sets/gold/labels/{first}", json={**GOOD, "china": None}).status_code == 422
    assert a.put(f"/api/sets/gold/labels/{first}", json=GOOD).status_code == 200
    assert b.post("/api/sets/gold/next", json={}).json()["item"]["key"] == first  # the pair is completed first
    assert b.put(f"/api/sets/gold/labels/{first}", json={**GOOD, "lean": 1}).status_code == 200
    assert c.post("/api/sets/gold/next", json={}).json()["item"]["key"] != first  # first has its two labels
    assert c.get(f"/api/sets/gold/items/{first}").status_code == 404  # neither open nor labeled by c

    assert a.get(f"/api/sets/gold/items/{first}").json()["label"]["lean"] == -2  # own label only
    assert a.put(f"/api/sets/gold/labels/{first}", json={**GOOD, "lean": -1, "seconds": 8}).status_code == 200
    saved = load(store, "labels/gold/a.json")["labels"][first]
    assert (saved["lean"], saved["rev"], saved["seconds"], saved["codebook"]) == (-1, 2, 50, "cb1")
    assert [r["key"] for r in a.get("/api/sets/gold/mine").json()["labels"]] == [first]
    assert a.get("/api/me").json()["sets"][0]["needed"] == 4

    doc = load(store, "labelers.json")
    doc["labelers"][0]["active"] = False  # revoke a
    save(store, "labelers.json", doc)
    assert a.get("/api/me").status_code == 401


def test_labelers_only_see_their_sets(store, monkeypatch):
    app = _site(store, monkeypatch)
    doc = load(store, "labelers.json")
    doc["labelers"][0]["sets"] = ["weekly"]
    save(store, "labelers.json", doc)
    a = _client(app, "a")
    assert a.get("/api/me").json()["sets"] == []
    assert a.post("/api/sets/gold/next", json={}).status_code == 404


def test_writes_must_come_from_the_site(store, monkeypatch):
    a = _client(_site(store, monkeypatch), "a")
    key = a.post("/api/sets/gold/next", json={}).json()["item"]["key"]
    url = f"/api/sets/gold/labels/{key}"
    assert a.put(url, json=GOOD, headers={"Origin": "https://evil.example"}).status_code == 403
    assert a.put(url, content="lean=5", headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code == 415
    assert a.put(url, json=GOOD, headers={"Origin": "http://testserver"}).status_code == 200


# --- back into the data repo ---------------------------------------------------------------


def test_sync_appends_once_in_the_label_app_format(store, data, monkeypatch):
    a = _client(_site(store, monkeypatch, n=2), "a")
    article = {"key0": "art0", "key1": "art1"}
    k1 = a.post("/api/sets/gold/next", json={}).json()["item"]["key"]
    a.put(f"/api/sets/gold/labels/{k1}", json=GOOD)
    k2 = a.post("/api/sets/gold/next", json={}).json()["item"]["key"]
    a.put(f"/api/sets/gold/labels/{k2}", json={"skipped": True, "skip_reason": "內容殘缺"})
    assert label_sync.run(store) == {"gold": {"labels": 2, "appended": 2, "coders": ["a"]}}
    assert label_sync.run(store)["gold"]["appended"] == 0
    a.put(f"/api/sets/gold/labels/{k1}", json={**GOOD, "lean": 4})  # a revision is appended and wins
    assert label_sync.run(store)["gold"]["appended"] == 1
    labels = load_labels("gold", "a")
    assert (labels[article[k1]]["lean"], labels[article[k1]]["coder"], labels[article[k1]]["source"]) == (4, "a", "site")
    assert labels[article[k2]]["skipped"] is True and labels[article[k2]]["lean"] is None
    state = json.loads((data / "state" / "labeling_sync.json").read_text())
    assert state["sets"]["gold"]["coders"] == ["a"] and state["synced_at"]


# --- fifth eval with several coders --------------------------------------------------------


def test_pooled_agreement():
    a, b = np.array([1, 2, 3, 4, 5, -2.0]), np.array([1, 3, 3, 5, 4, -1.0])
    assert evaluate.alpha_units([[x, y] for x, y in zip(a, b)]) == pytest.approx(evaluate.alpha_interval(a, b))
    humans = {
        "a": {f"x{i}": {"lean": v} for i, v in enumerate([-4, -2, 0, 2, 4, 1])},
        "b": {f"x{i}": {"lean": v} for i, v in enumerate([-5, -2, 1, 2, 3, 1])},
        "c": {"x0": {"lean": -4}, "x9": {"lean": 0}, "x8": {"lean": 3, "skipped": True}},
    }
    hh = evaluate.human_human(humans, "lean")
    assert hh["n"] == 6 and hh["pearson"] > 0.9 and 0.8 < hh["alpha"] <= 1
    model = {f"x{i}": {"lean": v} for i, v in enumerate([-4, -2, 0, 2, 4, 1])}
    assert evaluate.compare_pooled(humans, model, "lean", "lean")["n"] == 6 + 6 + 1


def test_all_coders_of_a_set(data):
    folder = data / "validation" / "labels"
    folder.mkdir(parents=True)
    for name in ("gold-a", "gold-b", "gold-2-c"):
        (folder / f"{name}.jsonl").write_text("")
    sample.save("gold", [])
    sample.save("gold-2", [])
    assert evaluate._coders("gold", "all") == ["a", "b"]
    assert evaluate._coders("gold", "a, b") == ["a", "b"]
