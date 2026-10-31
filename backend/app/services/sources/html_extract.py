"""Readability-lite HTML → Markdown, built on the standard library.

Why not BeautifulSoup/readability-libtm: the runtime must stay installable in an
offline environment (this project was developed without PyPI access), and the job
here is narrow. We need *clean prose plus heading structure* for chunking — not a
pixel-perfect renderer. A ~200 line ``HTMLParser`` subclass does that, has no
dependency, and is trivial to unit test with fixture strings.

The extraction rules are deliberately opinionated, because noise in the source
becomes noise in retrieval:

* script/style/nav/footer/aside/form/svg are dropped entirely;
* headings become Markdown headings — the chunker turns them into ``heading_path``,
  which is what citations display;
* lists become ``- item`` lines, tables become pipe-joined rows; both survive
  chunking better than a soup of ``<div>`` text;
* links keep only their anchor text (URLs are noise in embeddings);
* whitespace is normalised once at the end, never during traversal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

SKIP_TAGS = {
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "canvas",
    "nav",
    "footer",
    "aside",
    "form",
    "iframe",
    "button",
    "select",
    "header",
}
BLOCK_TAGS = {
    "p",
    "div",
    "section",
    "article",
    "main",
    "br",
    "tr",
    "table",
    "ul",
    "ol",
    "blockquote",
    "figure",
    "pre",
}
HEADING_TAGS = {f"h{level}": level for level in range(1, 7)}
VOID_TAGS = {"br", "img", "hr", "meta", "link", "input", "source", "col"}

# Class/id hints for chrome that is not wrapped in semantic tags (most sites ship
# <div class="xxx-nav"> instead of <nav>). Matched case-insensitively against the
# class and id attributes; the content wrapper itself never matches these.
_NOISE_ATTR = re.compile(
    r"nav|sidebar|menu|footer|sponsor|skip-link|visually-hidden|breadcrumb|"
    r"outline|toc|catalog|advert|banner|cookie|share|comment|related|toolbar|"
    r"spsr|aside|api-style|style-switch",
    re.IGNORECASE,
)
_STRUCTURAL = {"article", "main", "body", "html"}

_TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "spm",
    "fbclid",
    "gclid",
    "yclid",
    "ref",
    "ref_src",
}
# The web/sitemap crawlers extract *HTML* text. Binary assets and office files
# are skipped here rather than half-parsed: a PDF or .docx needs the document
# ingestion pipeline (upload or the connector's own file handling), not the
# readability extractor.
_SKIP_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".css", ".js",
    ".zip", ".rar", ".7z", ".mp4", ".mp3", ".woff", ".woff2", ".ttf", ".exe",
    ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx",
)
_MULTI_NEWLINE = re.compile(r"\n{3,}")
_TRAILING_SPACE = re.compile(r"[ \t]+\n")
_INLINE_SPACE = re.compile(r"[ \t]{2,}")


@dataclass
class ExtractedPage:
    title: str
    markdown: str
    links: list[str] = field(default_factory=list)


class _MarkdownExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[str] = []
        self.title = ""
        self._skip_depth = 0
        self._pre_depth = 0
        self._in_title = False
        self._link_href: str | None = None
        self._list_depth = 0
        self._hidden_stack: list[str] = []
        self._li_depth = 0

    # -- helpers -----------------------------------------------------------
    def _emit(self, text: str) -> None:
        self.parts.append(text)

    def _newline(self, count: int = 1) -> None:
        if self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n" * count)
        elif self.parts:
            tail = self.parts[-1]
            if tail.count("\n") < count and tail.strip() == "":
                self.parts[-1] = "\n" * count
        else:
            self.parts.append("")

    # -- parser hooks ------------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = dict(attrs)
        if tag == "title":
            self._in_title = True
            return
        if tag in SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return

        # Hidden nodes and chrome-by-class. ``hidden``/``aria-hidden`` is how many
        # docs sites ship "copy for LLM" blocks that must never reach the index.
        if "hidden" in attributes or attributes.get("aria-hidden") == "true":
            self._skip_depth += 1
            self._hidden_stack.append(tag)
            return
        if tag not in _STRUCTURAL:
            marker = f"{attributes.get('class') or ''} {attributes.get('id') or ''}"
            if marker.strip() and _NOISE_ATTR.search(marker):
                self._skip_depth += 1
                self._hidden_stack.append(tag)
                return

        if tag in HEADING_TAGS:
            self._newline(2)
            self._emit("#" * HEADING_TAGS[tag] + " ")
        elif tag == "li":
            self._li_depth += 1
            self._newline(1)
            self._emit("- ")
        elif tag == "blockquote":
            self._newline(2)
            self._emit("> ")
        elif tag == "pre":
            self._pre_depth += 1
            self._newline(2)
            self._emit("```\n")
        elif tag == "code" and not self._pre_depth:
            self._emit("`")
        elif tag == "a":
            href = attributes.get("href")
            if href:
                self._link_href = href
        elif tag in {"td", "th"}:
            self._emit(" | ")
        elif tag in BLOCK_TAGS:
            opens_block = tag in {"p", "div", "section", "article", "table"}
            # A block right after "- " is the item body: keep it on the same line
            # so the Markdown stays a real list item.
            if not (self._li_depth and self.parts and self.parts[-1].endswith("- ")):
                self._newline(1 if (opens_block and self._li_depth) else (2 if opens_block else 1))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
            return
        if tag in SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._hidden_stack and self._hidden_stack[-1] == tag:
            self._hidden_stack.pop()
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return

        if tag in HEADING_TAGS or tag in {"p", "blockquote"}:
            # A paragraph inside a list item is the item body, not a new block.
            self._newline(1 if self._li_depth else 2)
        elif tag == "li":
            self._li_depth = max(0, self._li_depth - 1)
            self._newline(1)
        elif tag == "pre":
            self._pre_depth = max(0, self._pre_depth - 1)
            self._newline(1)
            self._emit("```\n")
        elif tag == "code" and not self._pre_depth:
            self._emit("`")
        elif tag == "a":
            self._link_href = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data.strip() + " "
            return
        if self._skip_depth:
            return
        if self._pre_depth:
            self._emit(data)
            return
        text = data.replace("\xa0", " ")
        if not text.strip():
            if self.parts and not self.parts[-1].endswith((" ", "\n")):
                self._emit(" ")
            return
        self._emit(_INLINE_SPACE.sub(" ", text))


def _clean(markdown: str) -> str:
    # Zero-width spaces are used as anchor placeholders; they break embeddings
    # and make titles look duplicated in diffs.
    text = markdown.replace("\u200b", "").replace("\ufeff", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _TRAILING_SPACE.sub("\n", text)
    text = _MULTI_NEWLINE.sub("\n\n", text)
    lines = [line.rstrip() for line in text.split("\n")]
    # Drop lines that ended up as "| | |" from empty table cells.
    lines = [line for line in lines if line.strip() not in {"|", "||", "- |"}]
    return "\n".join(lines).strip()


def extract(html: str, base_url: str = "") -> ExtractedPage:
    """Convert one HTML document into ``(title, markdown, links)``."""
    parser = _MarkdownExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # pragma: no cover - HTMLParser is lenient, but stay safe
        pass

    links: list[str] = []
    seen: set[str] = set()
    for href in _collect_hrefs(html):
        absolute = normalize_url(urljoin(base_url, href) if base_url else href)
        if absolute and absolute not in seen and is_crawlable(absolute):
            seen.add(absolute)
            links.append(absolute)

    title = " ".join(parser.title.split()) or _fallback_title(parser.parts) or base_url
    return ExtractedPage(title=title, markdown=_clean("".join(parser.parts)), links=links)


def _collect_hrefs(html: str) -> list[str]:
    """Links come from a regex pass rather than the parser state.

    The Markdown extractor ignores anchors inside skipped subtrees (correctly),
    but sitemap-style discovery still wants every ``<a href>`` on the page.
    """
    return re.findall(r"""<a\s[^>]*href=["']([^"']+)["']""", html, flags=re.IGNORECASE)


def _fallback_title(parts: list[str]) -> str:
    for line in "".join(parts).split("\n"):
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def normalize_url(url: str) -> str:
    """Canonical form used for de-duplication and document identity."""
    if not url:
        return ""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        return ""
    query_pairs = []
    for pair in parts.query.split("&"):
        if not pair:
            continue
        key = pair.split("=", 1)[0].lower()
        if key in _TRACKING_PARAMS or key.startswith("utm_"):
            continue
        query_pairs.append(pair)
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return urlunsplit((parts.scheme, parts.netloc.lower(), path, "&".join(query_pairs), ""))


def is_crawlable(url: str) -> bool:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return False
    path = parts.path.lower()
    if path.endswith(_SKIP_EXTENSIONS):
        return False
    # Common non-content paths. Kept as a heuristic, not a guarantee.
    blocked = ("/login", "/signup", "/cart", "/checkout", "/search", "/tag/", "/author/")
    return not any(segment in path for segment in blocked)


def same_site(url: str, domains: list[str]) -> bool:
    if not domains:
        return True
    host = urlsplit(url).netloc.lower()
    return any(host == domain or host.endswith("." + domain) for domain in domains)
