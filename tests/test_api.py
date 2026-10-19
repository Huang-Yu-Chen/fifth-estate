import gzip
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from fifth_estate.api import hits, schema
from fifth_estate.api.app import create_app
from fifth_estate.api.snapshots import LocalFile, Snapshot, Store
from fifth_estate.export.serving import SnapshotWriter


def article(i: int, outlet: str) -> dict:
    return {"id": f"a{i:03d}", "outlet": outlet, "title": f"標題 {i}", "url": f"https://example.com/{i}", "lean": 0}


def build(path, snapshot_id="snap-1", n=25):
    with SnapshotWriter(path, snapshot_id) as w:
        w.doc("meta", {"generated_at": "2026-10-19T13:28+08:00", "fake_data": False})
        w.doc("index", {"all": [{"id": "all"}]})
        w.doc(schema.unit_doc("topic", "recall"), {"kind": "topic", "id": "recall", "windows": {}})
        w.doc(schema.outlet_doc("cna"), {"id": "cna"})
        for i in range(n):
            units = {"category": "domestic", "topic": "recall" if i % 2 else "budget"}
            if i < 5:
                units["event"] = "ev1"
            w.article(article(i, "cna" if i % 3 else "ltn"), ts=1_000_000 + i // 2, units=units)  # ts ties on purpose
        w.article(article(0, "cna"), ts=0, units={})  # duplicates are skipped
        w.finish(generated_at="2026-10-19T13:28+08:00", fake_data=False)
    return path


@pytest.fixture
def client(tmp_path):
    db = build(tmp_path / "serving.sqlite")
    store = Store(LocalFile(db), workdir=tmp_path)
    with TestClient(create_app(store)) as c:
        yield c


def test_writer_outputs(tmp_path):
    db = build(tmp_path / "serving.sqlite")
    assert db.exists() and not (tmp_path / "serving.sqlite.tmp").exists()
    assert gzip.decompress((tmp_path / "serving.sqlite.gz").read_bytes()) == db.read_bytes()
    snap = Snapshot(db, "v", owned=False)
    assert snap.id == "snap-1" and snap.info["articles"] == 25 and snap.info["schema"] == schema.SCHEMA_VERSION


def test_writer_failure_leaves_nothing(tmp_path):
    with pytest.raises(RuntimeError):
        with SnapshotWriter(tmp_path / "s.sqlite", "x") as w:
            w.doc("meta", {})
            raise RuntimeError("boom")
    assert list(tmp_path.iterdir()) == []


def test_documents_gzip_etag(client):
    r = client.get("/v1/meta")
    assert r.status_code == 200 and r.json()["generated_at"].startswith("2026-10-19")
    assert r.headers["content-encoding"] == "gzip" and r.headers["etag"] == 'W/"snap-1"'
    assert client.get("/v1/meta", headers={"If-None-Match": 'W/"snap-1"'}).status_code == 304
    plain = client.get("/v1/meta", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in plain.headers and plain.json()["fake_data"] is False
    assert client.get("/v1/units/topic/recall").json()["id"] == "recall"
    assert client.get("/v1/outlets/cna").json() == {"id": "cna"}
    assert client.get("/v1/units/topic/nope").status_code == 404
    assert client.get("/v1/units/bogus/recall").status_code == 404
    assert client.get("/v1/meta", headers={"Origin": "https://the-fifth-estate.org"}).headers["access-control-allow-origin"] == "*"


def test_articles_pages_and_counts(client):
    first = client.get("/v1/articles", params={"unit": "topic/recall", "limit": 5}).json()
    assert first["total"] == 12 and sum(first["outlets"].values()) == 12
    seen = [a["id"] for a in first["articles"]]
    cursor = first["next"]
    while cursor:
        page = client.get("/v1/articles", params={"unit": "topic/recall", "limit": 5, "cursor": cursor}).json()
        assert "total" not in page
        seen += [a["id"] for a in page["articles"]]
        cursor = page["next"]
    assert len(seen) == len(set(seen)) == 12
    assert seen == sorted(seen, reverse=True)  # newest first (ts ties broken by id)
    ltn = client.get("/v1/articles", params={"unit": "topic/recall", "outlet": "ltn"}).json()
    assert {a["outlet"] for a in ltn["articles"]} == {"ltn"} and ltn["total"] == 12  # counts ignore the outlet filter
    assert client.get("/v1/articles", params={"unit": "event/ev1"}).json()["total"] == 5
    everything = client.get("/v1/articles", params={"limit": 100}).json()
    assert everything["total"] == 25 and everything["next"] is None and everything["articles"][0]["title"] == "標題 24"
    assert client.get("/v1/articles", params={"unit": "topic"}).status_code == 400
    assert client.get("/v1/articles", params={"cursor": "%%%"}).status_code == 400


def test_health_and_missing_snapshot(tmp_path):
    store = Store(LocalFile(tmp_path / "missing.sqlite"), workdir=tmp_path)
    with TestClient(create_app(store)) as c:
        h = c.get("/v1/health")
        assert h.status_code == 503 and h.json()["ok"] is False and "does not exist" in h.json()["error"]
        assert c.get("/v1/meta").status_code == 503


def test_switches_to_new_snapshot(tmp_path):
    db = build(tmp_path / "serving.sqlite")
    now = [0.0]
    store = Store(LocalFile(db), workdir=tmp_path, check_seconds=60, clock=lambda: now[0])
    assert store.refresh() and store.snapshot.id == "snap-1"
    assert not store.refresh()  # same file
    build(db, snapshot_id="snap-2", n=3)
    now[0] = 61
    store.current()  # starts the background check
    for t in __import__("threading").enumerate():
        if t.name == "snapshot-refresh":
            t.join(5)
    assert store.snapshot.id == "snap-2"
    with store.reader() as (snap, con):
        assert con.execute("SELECT count(*) FROM articles").fetchone()[0] == 3


def test_replaced_download_deleted_after_last_reader(tmp_path):
    db = build(tmp_path / "a.sqlite")
    copy = tmp_path / "copy.sqlite"
    copy.write_bytes(db.read_bytes())
    snap = Snapshot(copy, "1", owned=True)
    snap.acquire()
    snap.retire()
    assert copy.exists()  # still being read
    snap.release()
    assert not copy.exists()


def test_hits(capsys):
    ua = "Mozilla/5.0 (Macintosh) Safari/605.1.15"
    now = datetime(2026, 10, 19, 16, 30, tzinfo=timezone.utc)  # 00:30 on the 20th in Taipei
    body = json.dumps({"route": "/unit/topic/recall?w=30", "referrer": "https://www.facebook.com/x?y=1"}).encode()
    e = hits.record(body, "203.0.113.7", ua, now)
    assert e["day"] == "2026-10-20" and e["route"] == "/unit/topic/recall" and e["referrer"] == "facebook.com"
    line = json.loads(capsys.readouterr().out)
    assert line == e and "203.0.113.7" not in json.dumps(line) and len(line["visitor"]) == 16
    assert hits.record(body, "203.0.113.7", ua, now)["visitor"] == e["visitor"]  # same day, same visitor
    assert hits.visitor("203.0.113.7", ua, "2026-10-21") != e["visitor"]  # a new salt every day
    same_site = json.dumps({"route": "/events", "referrer": "https://the-fifth-estate.org/#/"}).encode()
    assert hits.record(same_site, "1.1.1.1", ua, now)["referrer"] == ""
    assert hits.record(body, "1.1.1.1", "Googlebot/2.1", now) is None
    assert hits.record(body, "1.1.1.1", "", now) is None
    assert hits.record(b"not json", "1.1.1.1", ua, now) is None
    assert hits.record(json.dumps({"route": "events"}).encode(), "1.1.1.1", ua, now) is None


def test_hit_endpoint(client, capsys):
    r = client.post("/v1/hit", content=json.dumps({"route": "/events", "referrer": ""}),
                    headers={"User-Agent": "Mozilla/5.0 Firefox/140.0", "X-Forwarded-For": "198.51.100.1, 203.0.113.9",
                             "Content-Type": "text/plain;charset=UTF-8"})
    assert r.status_code == 204
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["event"] == "hit" and line["route"] == "/events"
    assert line["visitor"] == hits.visitor("203.0.113.9", "Mozilla/5.0 Firefox/140.0", line["day"])


def test_unexpected_error_is_logged_500_with_cors(client, capsys):
    def broken():
        raise RuntimeError("boom")

    client.app.state.store.reader = broken
    r = client.get("/v1/meta", headers={"Origin": "https://the-fifth-estate.org"})
    assert r.status_code == 500 and r.headers["access-control-allow-origin"] == "*"
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["severity"] == "ERROR" and "RuntimeError: boom" in line["message"]
