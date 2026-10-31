"""Connector and sync helpers: no network, no database.

Everything here is a pure function, which is deliberate — the crawling code is
hard to test end-to-end in CI (network flakiness, someone else's site changing),
so the parsing/normalisation/diff logic is factored out until it can be tested
with strings.
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.schemas.source import SourceCreate, validate_config
from app.services.source_service import compute_next_run, content_digest, decide_action
from app.services.sources.html_extract import (
    extract,
    is_crawlable,
    normalize_url,
    same_site,
)

ARTICLE = """
<html>
  <head><title>  X1 使用手册  </title><style>.a{color:red}</style></head>
  <body>
    <nav><a href="/nav">导航</a>菜单</nav>
    <article>
      <h1>X1 使用手册</h1>
      <p>X1 支持 <strong>IP68</strong> 防水，电池容量为 5000mAh。</p>
      <h2>充电</h2>
      <ul><li>支持 65W 快充</li><li>兼容 PD 协议</li></ul>
      <pre><code>charge --fast</code></pre>
    </article>
    <footer>版权所有</footer>
    <a href="/detail?id=2&utm_source=weixin">下一节</a>
  </body>
</html>
"""


def test_extract_keeps_structure_and_drops_chrome():
    page = extract(ARTICLE, "https://docs.example.com/x1/manual")

    assert page.title == "X1 使用手册"
    assert "# X1 使用手册" in page.markdown
    assert "## 充电" in page.markdown
    assert "- 支持 65W 快充" in page.markdown
    assert "charge --fast" in page.markdown
    # Navigation/footer/style must not reach the knowledge base.
    assert "导航" not in page.markdown
    assert "版权所有" not in page.markdown
    assert "color:red" not in page.markdown


def test_extract_resolves_links_and_strips_tracking_params():
    page = extract(ARTICLE, "https://docs.example.com/x1/manual")
    assert "https://docs.example.com/detail?id=2" in page.links
    assert all("utm_source" not in link for link in page.links)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://A.com/path/?utm_source=x&b=1#frag", "https://a.com/path?b=1"),
        ("https://a.com", "https://a.com/"),
        ("mailto:x@y.com", ""),
        ("javascript:void(0)", ""),
        ("/relative", ""),
    ],
)
def test_normalize_url(raw, expected):
    assert normalize_url(raw) == expected


@pytest.mark.parametrize(
    ("url", "crawlable"),
    [
        ("https://a.com/docs/guide", True),
        ("https://a.com/logo.png", False),
        ("https://a.com/file.pdf", False),
        ("https://a.com/login", False),
    ],
)
def test_is_crawlable(url, crawlable):
    assert is_crawlable(url) is crawlable


def test_same_site_matches_subdomains_only():
    assert same_site("https://docs.a.com/x", ["a.com"])
    assert not same_site("https://evil-a.com/x", ["a.com"])


def test_content_digest_is_stable_and_sensitive():
    assert content_digest("abc") == content_digest("abc")
    assert content_digest("abc") != content_digest("abd")


@pytest.mark.parametrize(
    ("existing", "new", "action"),
    [
        (None, "h1", "create"),
        ("h1", "h1", "unchanged"),
        ("h1", "h2", "update"),
    ],
)
def test_decide_action(existing, new, action):
    assert decide_action(existing, new) == action


def test_compute_next_run():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert compute_next_run(0, now) is None
    assert compute_next_run(30, now) == now + timedelta(minutes=30)


def test_source_create_validates_connector_config():
    source = SourceCreate(
        name="docs",
        source_type="web",
        config={"start_urls": ["https://docs.example.com"], "max_pages": 5},
    )
    assert source.config["max_pages"] == 5
    assert source.config["respect_robots"] is True

    with pytest.raises(ValueError):
        SourceCreate(name="bad", source_type="web", config={})

    with pytest.raises(ValueError):
        validate_config("github", {"repo": "not-a-repo"})

    with pytest.raises(ValueError):
        validate_config("rss", {"feed_url": "https://x.com/feed", "max_items": 0})


def test_source_create_rejects_unknown_type():
    with pytest.raises(ValidationError):
        SourceCreate(name="x", source_type="ftp", config={})


def test_extract_drops_hidden_and_chrome_by_class():
    html = """
    <body>
      <a class="VPSkipLink visually-hidden">直接跳到内容</a>
      <div class="VPLocalNavOutlineDropdown">本页目录</div>
      <div hidden="true">Are you an LLM? internal note</div>
      <div class="spsr-container special aside">成为赞助商</div>
      <main><div class="vt-doc"><h1>正文标题</h1><p>唯一应该保留的段落。</p></div></main>
    </body>
    """
    page = extract(html, "https://e.com/x")
    assert "直接跳到内容" not in page.markdown
    assert "本页目录" not in page.markdown
    assert "internal note" not in page.markdown
    assert "成为赞助商" not in page.markdown
    assert "唯一应该保留的段落" in page.markdown


def test_extract_keeps_list_items_on_one_line_and_strips_zero_width():
    html = (
        "<article><ul><li><p>第一项</p></li><li><p>第二项</p></li></ul>"
        "<h2>标题\u200b</h2></article>"
    )
    page = extract(html, "https://e.com/x")
    assert "- 第一项" in page.markdown
    assert "- 第二项" in page.markdown
    assert "\u200b" not in page.markdown


def test_extract_drops_chrome_before_the_first_heading():
    """A promo banner with no noise class still lands before the H1.

    Vite's docs open every page with a conference banner whose only markup hint
    is ``class="viteconf"``; the noise-attribute heuristic cannot see it, so the
    rule has to be positional: docs bodies start at the H1.
    """
    html = (
        "<body>"
        '<a class="viteconf" href="https://viteconf.org/">'
        '<p class="extra-info">Building Together</p>'
        '<p class="heading">ViteConf 2026</p>'
        '<p class="extra-info">October 15, Online</p>'
        "</a>"
        "<main><h1>构建选项</h1><p>本节中的选项仅适用于构建。</p></main>"
        "</body>"
    )
    page = extract(html, "https://e.com/x")
    assert "Building Together" not in page.markdown
    assert "ViteConf" not in page.markdown
    assert page.markdown.startswith("# 构建选项")


def test_extract_keeps_an_intro_that_has_no_heading():
    """No heading means no anchor; the rule must not delete the whole page."""
    html = "<body><p>这一段就是正文，页面没有标题。</p><p>第二段。</p></body>"
    page = extract(html, "https://e.com/x")
    assert "这一段就是正文" in page.markdown
    assert "第二段" in page.markdown


def test_extract_keeps_a_long_lead_in_paragraph():
    """A heading deep into the page is not an H1; the text above it is the intro."""
    lead = "这是一段很长的引言。" * 40
    html = f"<body><p>{lead}</p><h2>小标题</h2><p>后续内容。</p></body>"
    page = extract(html, "https://e.com/x")
    assert "这是一段很长的引言。" in page.markdown
