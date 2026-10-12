from datetime import datetime, timezone

from fifth_estate.config import Outlet, Source
from fifth_estate.ingest import dates
from fifth_estate.ingest.clean import clean_body, clean_title
from fifth_estate.ingest.extract import extract
from fifth_estate.ingest.urls import article_id, canonicalize


def _outlet(**kw) -> Outlet:
    base = dict(id="x", name="測試報", short="測試", self_names=("測試報", "中央社"), sources=(Source(type="rss", url="u"),))
    base.update(kw)
    return Outlet(**base)


# --- dates -----------------------------------------------------------------

def test_parse_iso_with_offset():
    assert dates.parse("2026-10-13T01:16:03+08:00") == datetime(2026, 10, 12, 17, 16, 3, tzinfo=timezone.utc)


def test_parse_rss_date():
    assert dates.parse("Mon,12 Oct 2026 23:41:00  +0800") == datetime(2026, 10, 12, 15, 41, tzinfo=timezone.utc)


def test_parse_utc_z():
    assert dates.parse("2026-10-12T16:31:46.000Z") == datetime(2026, 10, 12, 16, 31, 46, tzinfo=timezone.utc)


def test_setn_naive_local_quirk():
    # 三立 labels Taipei time as +00:00
    assert dates.parse("2026-10-13 01:30 +00:00", quirk="naive_local") == datetime(2026, 10, 12, 17, 30, tzinfo=timezone.utc)


def test_escaped_plus_and_naive_default_taipei():
    assert dates.parse("2026-10-13T08:00:00&#43;08:00") == datetime(2026, 10, 13, 0, 0, tzinfo=timezone.utc)
    assert dates.parse("2026-10-13 08:00") == datetime(2026, 10, 13, 0, 0, tzinfo=timezone.utc)


def test_plausible_rejects_future():
    now = datetime(2026, 10, 13, tzinfo=timezone.utc)
    assert not dates.plausible(datetime(2026, 10, 13, 8, tzinfo=timezone.utc), now=now)
    assert dates.plausible(datetime(2026, 10, 12, 8, tzinfo=timezone.utc), now=now)


# --- urls ------------------------------------------------------------------

def test_canonicalize_drops_tracking_and_amp():
    a = canonicalize("http://news.ltn.com.tw/amp/news/politics/breakingnews/5604170?utm_source=fb#top")
    assert a == "https://news.ltn.com.tw/news/politics/breakingnews/5604170"


def test_canonicalize_percent_encoding_equal():
    raw = "https://tw.news.yahoo.com/韓國瑜-160730408.html"
    enc = "https://tw.news.yahoo.com/%E9%9F%93%E5%9C%8B%E7%91%9C-160730408.html"
    assert canonicalize(raw) == canonicalize(enc)


def test_article_id_stable():
    assert article_id("ltn", "https://a/b") == article_id("ltn", "https://a/b")
    assert article_id("ltn", "https://a/b") != article_id("udn", "https://a/b")


# --- cleaning --------------------------------------------------------------

def test_clean_removes_bylines_credits_and_self_names():
    body = (
        "〔記者王小明／台北報導〕立法院今天三讀通過法案。\n"
        "▲立法院議場。（圖／記者攝）\n"
        "（中央社記者李四台北12日電）行政院表示將覆議。測試報調查顯示民眾支持。\n"
        "延伸閱讀\n"
        "無關的推薦文章標題"
    )
    out = clean_body(body, ("測試報", "中央社"))
    assert "王小明" not in out and "李四" not in out and "推薦文章" not in out
    assert "立法院今天三讀通過法案。" in out
    assert "本媒體調查顯示民眾支持" in out


def test_clean_long_single_line_keeps_content():
    # JSON-LD bodies often arrive as one long line; a hotline sentence must not wipe the article.
    body = "某旅女性中尉日前墜樓。" * 20 + "珍惜生命，自殺不能解決問題，安心專線1925。"
    out = clean_body(body)
    assert out.startswith("某旅女性中尉日前墜樓。")
    assert "1925" not in out


def test_clean_title_prefix():
    assert clean_title("快訊／影音／國防條例三讀") == "國防條例三讀"


# --- extraction --------------------------------------------------------------

PAGE = """<html><head>
<meta property="og:title" content="國防條例三讀 | 政治 | 測試報">
<meta property="article:published_time" content="2026-10-12T21:44:00+08:00">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"NewsArticle",
 "headline":"國防條例三讀",
 // comment like ETtoday
 "datePublished":"2026-10-12T21:44:00+08:00","articleSection":"政治",
 "author":{"@type":"Person","name":"王小明"},
 "articleBody":"立法院今天三讀通過國防特別條例。在野黨表示將持續監督。"}</script>
<script type="application/ld+json">{"@type":"BreadcrumbList","itemListElement":[
 {"@type":"ListItem","position":1,"name":"測試報"},{"@type":"ListItem","position":2,"name":"要聞"},
 {"@type":"ListItem","position":3,"name":"政治"},{"@type":"ListItem","position":4,"name":"國防條例三讀"}]}</script>
</head><body><article><p>立法院今天三讀通過國防特別條例。</p><p>在野黨表示將持續監督。</p></article></body></html>"""


def test_extract_jsonld_breadcrumb_and_lenient_json():
    ex = extract(PAGE, "https://example.com/a/1", _outlet())
    assert ex.title == "國防條例三讀"
    assert ex.section == "要聞>政治"
    assert ex.published_at == datetime(2026, 10, 12, 13, 44, tzinfo=timezone.utc)
    assert ex.byline == ["王小明"]
    assert "國防特別條例" in ex.body
