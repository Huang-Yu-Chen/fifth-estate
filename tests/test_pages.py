import json
import threading

import pytest
from fastapi.testclient import TestClient

from fifth_estate.api import pages, schema
from fifth_estate.api.app import create_app
from fifth_estate.api.snapshots import LocalFile, Store
from fifth_estate.export.serving import SnapshotWriter

SITE = "https://the-fifth-estate.org"
COMPARE = "比較各家媒體的報導量、綠藍傾向、抗中友中立場與極化指數。"


def page_template(indexable: bool) -> str:
    """Shaped like web/vite.config.ts html() with the @@…@@ slots."""
    robots = "" if indexable else '<meta name="robots" content="noindex" />\n    '
    return (
        '<!doctype html>\n<html lang="zh-Hant">\n  <head>\n'
        '    <meta name="description" content="@@DESCRIPTION@@" />\n'
        "    <title>@@NAME@@｜第五權</title>\n"
        '    <script type="module" crossorigin src="/assets/index-abc123.js"></script>\n'
        f'    {robots}<link rel="canonical" href="{SITE}@@PATH@@" />\n'
        '    <meta property="og:title" content="@@NAME@@" />\n'
        '    <meta property="og:description" content="@@DESCRIPTION@@" />\n'
        f'    <meta property="og:url" content="{SITE}@@PATH@@" />\n'
        '  </head>\n  <body><div id="root"></div></body>\n</html>\n'
    )


TEMPLATES = {
    "site_url": SITE,
    "pages": ["/", "/coverage", "/grid", "/events", "/methodology"],
    "page": {"index": page_template(True), "noindex": page_template(False)},
    "not_found": '<!doctype html><html><head><meta name="robots" content="noindex" /><title>第五權</title></head></html>',
    "describe": {
        "event": f"新聞事件「{{name}}」：{COMPARE}",
        "unit": f"{{name}}：{{detail}}。{COMPARE}",
        "unit_plain": f"{{name}}：{COMPARE}",
        "outlet": "{name}的政治、兩岸與外交新聞。",
    },
    "assets": ["assets/index-abc123.js"],
}


def build(path, status="beta", fake=False, snapshot_id="snap-1"):
    with SnapshotWriter(path, snapshot_id) as w:
        w.doc("meta", {"fake_data": fake, "site": {"status": status}, "outlets": [{"id": "cna", "name": "中央社"}]})
        w.doc("index", {"topic": [{"id": "recall", "name": "罷免"}], "event": [{"id": "ev261020-9c37d2", "name": "新事件"}]})
        w.doc(schema.unit_doc("topic", "recall"), {"kind": "topic", "id": "recall", "name": "罷免", "description": "大罷免與投票"})
        w.doc(schema.unit_doc("category", "domestic"), {"kind": "category", "id": "domestic", "name": "內政"})
        w.doc(schema.unit_doc("event", "ev261020-9c37d2"), {"kind": "event", "id": "ev261020-9c37d2", "name": '立法院"<三讀>"&通過 @@PATH@@'})
        w.doc(schema.outlet_doc("cna"), {"id": "cna", "name": "中央社"})
        w.finish(generated_at="2026-10-20T08:13+08:00", fake_data=fake)
    return path


def make_client(tmp_path, templates_file=None, **snap):
    db = build(tmp_path / "serving.sqlite", **snap)
    if templates_file is None:
        templates_file = tmp_path / "page-templates.json"
        templates_file.write_text(json.dumps(TEMPLATES, ensure_ascii=False), encoding="utf-8")
    store = Store(LocalFile(db), workdir=tmp_path)
    return TestClient(create_app(store, pages.Templates(str(templates_file))))


@pytest.fixture
def client(tmp_path):
    with make_client(tmp_path) as c:
        yield c


def test_new_event_page_has_its_own_head(client):
    r = client.get("/unit/event/ev261020-9c37d2")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    body = r.text
    name = "立法院&quot;&lt;三讀&gt;&quot;&amp;通過 @@PATH@@"  # escaped, and never filled in twice
    assert f"<title>{name}｜第五權</title>" in body
    assert f'<link rel="canonical" href="{SITE}/unit/event/ev261020-9c37d2" />' in body
    assert f'<meta property="og:url" content="{SITE}/unit/event/ev261020-9c37d2" />' in body
    assert f'<meta property="og:title" content="{name}" />' in body
    assert f'<meta property="og:description" content="新聞事件「{name}」：{COMPARE}" />' in body
    assert '<meta name="robots" content="noindex" />' in body  # beta
    assert "/assets/index-abc123.js" in body
    assert "public" in r.headers["cache-control"] and r.headers["etag"].startswith('W/"snap-1.')
    assert client.get("/unit/event/ev261020-9c37d2", headers={"If-None-Match": r.headers["etag"]}).status_code == 304
    head = client.head("/unit/event/ev261020-9c37d2")
    assert head.status_code == 200 and head.content == b""


def test_unit_descriptions(client):
    topic = client.get("/unit/topic/recall").text
    assert f'<meta name="description" content="罷免：大罷免與投票。{COMPARE}" />' in topic
    category = client.get("/unit/category/domestic").text
    assert f'<meta name="description" content="內政：{COMPARE}" />' in category
    outlet = client.get("/outlet/cna")
    assert outlet.status_code == 200 and "<title>中央社｜第五權</title>" in outlet.text
    assert f'href="{SITE}/outlet/cna"' in outlet.text and "中央社的政治、兩岸與外交新聞。" in outlet.text


@pytest.mark.parametrize("path", ["/unit/event/ev-gone", "/unit/bogus/recall", "/unit/topic", "/unit/topic/recall/x",
                                  "/unit/topic/re%20call", "/unit", "/unit/", "/outlet/nope", "/outlet", "/outlet/cna/x"])
def test_unknown_pages_are_404(client, path):
    r = client.get(path)
    assert r.status_code == 404 and r.text == TEMPLATES["not_found"] and r.headers["cache-control"] == "no-store"


def test_trailing_slash_redirects_on_the_site(client):
    r = client.get("/unit/topic/recall/?w=30", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/unit/topic/recall?w=30"


def test_beta_robots_and_no_sitemap(client):
    robots = client.get("/robots.txt")
    assert robots.status_code == 200 and robots.text == "User-agent: *\nAllow: /\n"
    assert client.get("/sitemap.xml").status_code == 404


def test_stable_site_is_indexable(tmp_path):
    with make_client(tmp_path, status="stable") as c:
        assert "noindex" not in c.get("/unit/topic/recall").text
        assert c.get("/robots.txt").text.endswith(f"\nSitemap: {SITE}/sitemap.xml\n")
        sitemap = c.get("/sitemap.xml")
        assert sitemap.status_code == 200 and sitemap.headers["content-type"].startswith("application/xml")
        locs = [line.strip()[len("<url><loc>"):-len("</loc></url>")] for line in sitemap.text.splitlines() if "<loc>" in line]
        assert locs == [f"{SITE}{p}" for p in ["/", "/coverage", "/grid", "/events", "/methodology",
                                                "/unit/topic/recall", "/unit/event/ev261020-9c37d2", "/outlet/cna"]]


def test_fake_data_is_never_indexable(tmp_path):
    with make_client(tmp_path, status="stable", fake=True) as c:
        assert "noindex" in c.get("/unit/topic/recall").text
        assert c.get("/sitemap.xml").status_code == 404


def test_without_templates_pages_wait_but_data_is_served(tmp_path):
    with make_client(tmp_path, templates_file=tmp_path / "missing.json") as c:
        r = c.get("/unit/topic/recall")
        assert r.status_code == 503 and r.headers["retry-after"] == "30"
        assert c.get("/v1/meta").status_code == 200
        health = c.get("/v1/health").json()
        assert health["ok"] and "missing.json" in health["page_templates"]["error"]


def test_templates_switch_and_bad_ones_are_refused(tmp_path):
    f = tmp_path / "page-templates.json"
    f.write_text(json.dumps(TEMPLATES), encoding="utf-8")
    now = [0.0]
    t = pages.Templates(str(f), check_seconds=60, clock=lambda: now[0])
    assert t.refresh() and not t.refresh()
    first = t.version
    f.write_text("{}", encoding="utf-8")  # e.g. a broken deploy
    assert not t.refresh() and t.version == first and "not a page-templates.json" in t.error
    f.write_text(json.dumps({**TEMPLATES, "assets": ["assets/index-new.js"]}), encoding="utf-8")
    now[0] = 61
    t.current()  # starts the background check
    for th in threading.enumerate():
        if th.name == "templates-refresh":
            th.join(5)
    assert t.version != first and t.doc["assets"] == ["assets/index-new.js"] and t.error is None
