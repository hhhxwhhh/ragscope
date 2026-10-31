"""Connector registry.

Adding a source type means: subclass ``Fetcher``, append it here. Everything
downstream (API validation, sync service, scheduler, UI) is driven by the type
string, so there is exactly one place to register a new connector.
"""

from __future__ import annotations

from app.services.sources.base import Fetcher, FetchError, FetchOutcome, SourceItem
from app.services.sources.github import GithubFetcher
from app.services.sources.web import RssFetcher, SitemapFetcher, WebFetcher

FETCHERS: dict[str, Fetcher] = {
    "web": WebFetcher(),
    "sitemap": SitemapFetcher(),
    "rss": RssFetcher(),
    "github": GithubFetcher(),
}

SOURCE_TYPES = tuple(FETCHERS)


def get_fetcher(source_type: str) -> Fetcher:
    fetcher = FETCHERS.get(source_type)
    if fetcher is None:
        raise FetchError(f"未知的数据源类型：{source_type}")
    return fetcher


async def fetch(source_type: str, config: dict) -> FetchOutcome:
    return await get_fetcher(source_type).fetch(config)


__all__ = [
    "FETCHERS",
    "SOURCE_TYPES",
    "Fetcher",
    "FetchError",
    "FetchOutcome",
    "SourceItem",
    "fetch",
    "get_fetcher",
]
