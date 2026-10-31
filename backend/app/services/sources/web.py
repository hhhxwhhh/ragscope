"""Web crawling connectors: breadth-first site crawl, sitemap and RSS.

The three share an HTTP client, a robots cache and the same extractor, and differ
only in how they discover URLs:

* ``web`` — BFS from the start URLs, bounded by depth and page count;
* ``sitemap`` — read ``sitemap.xml`` (including one level of sitemap index) and
  fetch a capped number of entries; good for documentation sites;
* ``rss`` — read a feed and use the entry content directly, no page fetch needed.

Bounds are the point. An unbounded crawler attached to a scheduled job is a
denial-of-service tool pointed at someone else's server (and at your own token
budget). ``max_pages`` and ``max_depth`` are validated at the API boundary and
enforced again here.
"""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from collections import deque
from typing import Any
from urllib.parse import urljoin, urlsplit

import feedparser
import httpx

from app.services.sources.base import Fetcher, FetchOutcome, SourceItem, ca_bundle
from app.services.sources.html_extract import (
    extract,
    is_crawlable,
    normalize_url,
    same_site,
)
from app.services.sources.robots import DEFAULT_UA, RobotsCache

logger = logging.getLogger(__name__)

MAX_PAGES_HARD_LIMIT = 200
MIN_CONTENT_CHARS = 80


def _client(
    user_agent: str = DEFAULT_UA, timeout: float = 20.0, verify_tls: bool = True
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        verify=ca_bundle() if verify_tls else False,
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )


def _domains(config: dict[str, Any], start_urls: list[str]) -> list[str]:
    configured = [item.strip().lower() for item in config.get("allowed_domains") or [] if item]
    if configured:
        return configured
    return sorted({urlsplit(url).netloc.lower() for url in start_urls if url})


def _matches_filters(url: str, config: dict[str, Any]) -> bool:
    include = [pattern for pattern in config.get("include_patterns") or [] if pattern]
    exclude = [pattern for pattern in config.get("exclude_patterns") or [] if pattern]
    if include and not any(pattern in url for pattern in include):
        return False
    return not any(pattern in url for pattern in exclude)


class WebFetcher(Fetcher):
    source_type = "web"

    async def fetch(self, config: dict[str, Any]) -> FetchOutcome:
        start_urls = [normalize_url(url) for url in config.get("start_urls") or []]
        start_urls = [url for url in start_urls if url]
        if not start_urls:
            raise ValueError("web 数据源至少需要一个有效的 start_urls")

        max_pages = min(int(config.get("max_pages") or 20), MAX_PAGES_HARD_LIMIT)
        max_depth = min(int(config.get("max_depth") or 1), 3)
        delay = float(config.get("request_delay_seconds") or 0.8)
        respect_robots = bool(config.get("respect_robots", True))
        domains = _domains(config, start_urls)

        outcome = FetchOutcome()
        seen: set[str] = set()
        queue: deque[tuple[str, int]] = deque((url, 0) for url in start_urls)

        async with _client(
            config.get("user_agent") or DEFAULT_UA,
            verify_tls=bool(config.get("verify_tls", True)),
        ) as client:
            robots = RobotsCache(client, config.get("user_agent") or DEFAULT_UA)

            while queue and len(outcome.items) < max_pages:
                url, depth = queue.popleft()
                if url in seen:
                    continue
                seen.add(url)

                if respect_robots and not await robots.allowed(url):
                    outcome.add_error(url, "robots.txt 禁止抓取")
                    continue

                try:
                    response = await client.get(url)
                except Exception as exc:
                    outcome.add_error(url, f"{exc.__class__.__name__}: {exc}")
                    continue

                outcome.pages_fetched += 1
                content_type = response.headers.get("content-type", "")
                if response.status_code >= 400:
                    outcome.add_error(url, f"HTTP {response.status_code}")
                    continue
                if "html" not in content_type and "xml" not in content_type:
                    outcome.add_error(url, f"不支持的内容类型：{content_type or 'unknown'}")
                    continue

                page = extract(response.text, str(response.url))
                if len(page.markdown) < MIN_CONTENT_CHARS:
                    outcome.add_error(url, "正文过短，可能是导航页或需要 JS 渲染")
                    continue

                outcome.items.append(
                    SourceItem(
                        external_id=normalize_url(str(response.url)),
                        title=page.title,
                        content=page.markdown,
                        metadata={"url": str(response.url), "depth": depth},
                    )
                )

                if depth >= max_depth:
                    delay and await asyncio.sleep(delay)
                    continue

                for link in page.links:
                    if link in seen:
                        continue
                    if not same_site(link, domains) or not is_crawlable(link):
                        continue
                    if not _matches_filters(link, config):
                        continue
                    queue.append((link, depth + 1))

                if delay:
                    await robots.wait(url, delay)

        return outcome


class SitemapFetcher(Fetcher):
    source_type = "sitemap"

    async def fetch(self, config: dict[str, Any]) -> FetchOutcome:
        sitemap_url = normalize_url(str(config.get("sitemap_url") or ""))
        if not sitemap_url:
            raise ValueError("sitemap 数据源需要 sitemap_url")

        max_pages = min(int(config.get("max_pages") or 20), MAX_PAGES_HARD_LIMIT)
        delay = float(config.get("request_delay_seconds") or 0.5)
        outcome = FetchOutcome()

        async with _client(
            config.get("user_agent") or DEFAULT_UA,
            verify_tls=bool(config.get("verify_tls", True)),
        ) as client:
            urls = await _read_sitemap(client, sitemap_url, outcome)
            if config.get("include_patterns"):
                urls = [url for url in urls if _matches_filters(url, config)]
            urls = urls[:max_pages]

            robots = RobotsCache(client, config.get("user_agent") or DEFAULT_UA)
            for url in urls:
                if config.get("respect_robots", True) and not await robots.allowed(url):
                    outcome.add_error(url, "robots.txt 禁止抓取")
                    continue
                try:
                    response = await client.get(url)
                except Exception as exc:
                    outcome.add_error(url, f"{exc.__class__.__name__}: {exc}")
                    continue
                outcome.pages_fetched += 1
                if response.status_code >= 400:
                    outcome.add_error(url, f"HTTP {response.status_code}")
                    continue
                page = extract(response.text, str(response.url))
                if len(page.markdown) < MIN_CONTENT_CHARS:
                    outcome.add_error(url, "正文过短，跳过")
                    continue
                outcome.items.append(
                    SourceItem(
                        external_id=normalize_url(str(response.url)),
                        title=page.title,
                        content=page.markdown,
                        metadata={"url": str(response.url)},
                    )
                )
                if delay:
                    await robots.wait(url, delay)

        return outcome


async def _read_sitemap(
    client: httpx.AsyncClient, sitemap_url: str, outcome: FetchOutcome
) -> list[str]:
    try:
        response = await client.get(sitemap_url)
        response.raise_for_status()
    except Exception as exc:
        outcome.add_error(sitemap_url, f"sitemap 读取失败：{exc.__class__.__name__}")
        return []

    root = _parse_xml(response.text)
    if root is None:
        outcome.add_error(sitemap_url, "sitemap XML 解析失败")
        return []

    locs = [
        element.text.strip()
        for element in root.iter()
        if element.tag.endswith("loc") and element.text
    ]
    # A sitemap index points at other sitemaps; follow one level only.
    if root.tag.endswith("sitemapindex"):
        collected: list[str] = []
        for child in locs[:5]:
            try:
                child_response = await client.get(child)
                child_root = _parse_xml(child_response.text)
                if child_root is None:
                    continue
                collected.extend(
                    element.text.strip()
                    for element in child_root.iter()
                    if element.tag.endswith("loc") and element.text
                )
            except Exception as exc:
                outcome.add_error(child, f"子 sitemap 读取失败：{exc.__class__.__name__}")
        return [normalize_url(url) for url in collected if normalize_url(url)]
    return [normalize_url(url) for url in locs if normalize_url(url)]


def _parse_xml(text: str) -> ET.Element | None:
    try:
        return ET.fromstring(text.strip())
    except ET.ParseError:
        return None


class RssFetcher(Fetcher):
    source_type = "rss"

    async def fetch(self, config: dict[str, Any]) -> FetchOutcome:
        feed_url = normalize_url(str(config.get("feed_url") or ""))
        if not feed_url:
            raise ValueError("rss 数据源需要 feed_url")

        max_items = min(int(config.get("max_items") or 20), 100)
        outcome = FetchOutcome()

        async with _client(
            config.get("user_agent") or DEFAULT_UA,
            verify_tls=bool(config.get("verify_tls", True)),
        ) as client:
            try:
                response = await client.get(feed_url)
                response.raise_for_status()
            except Exception as exc:
                raise ValueError(f"RSS 读取失败：{exc.__class__.__name__}: {exc}") from exc
            outcome.pages_fetched = 1

        parsed = feedparser.parse(response.content)
        if parsed.bozo and not parsed.entries:
            raise ValueError("RSS 解析失败，内容可能不是合法 feed")

        for entry in parsed.entries[:max_items]:
            link = normalize_url(entry.get("link", "")) or normalize_url(feed_url)
            title = (entry.get("title") or link).strip()
            raw = entry.get("content")[0].get("value") if entry.get("content") else ""
            raw = raw or entry.get("summary") or entry.get("description") or ""
            page = extract(raw, link)
            body = page.markdown or raw
            if len(body) < MIN_CONTENT_CHARS:
                outcome.add_error(link, "条目正文过短，跳过")
                continue
            outcome.items.append(
                SourceItem(
                    external_id=link,
                    title=title,
                    content=f"# {title}\n\n{body}",
                    metadata={
                        "url": link,
                        "published": entry.get("published") or entry.get("updated"),
                        "feed": parsed.feed.get("title"),
                    },
                )
            )
        return outcome


def _join(base: str, path: str) -> str:
    return urljoin(base.rstrip("/") + "/", path.lstrip("/"))
