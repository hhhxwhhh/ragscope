"""robots.txt handling.

Politeness is not optional when a feature exists to fetch other people's pages on
a schedule. We check ``robots.txt`` per host, honour ``Crawl-delay`` when it is
present, and default to *deny on parse failure only if the file explicitly
disallows* — an unreachable robots.txt is treated as "no restriction", which is
the conventional interpretation but is still worth stating out loud.
"""

from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

logger = logging.getLogger(__name__)

DEFAULT_UA = "RAGScopeBot/0.5 (+https://github.com/; knowledge-base sync)"


class RobotsCache:
    """Per-run cache of parsed robots files. One instance per sync."""

    def __init__(self, client: httpx.AsyncClient, user_agent: str = DEFAULT_UA) -> None:
        self._client = client
        self._user_agent = user_agent
        self._parsers: dict[str, RobotFileParser | None] = {}
        self._delays: dict[str, float] = {}
        self._fetched_at: dict[str, float] = {}

    async def _load(self, scheme: str, host: str) -> RobotFileParser | None:
        if host in self._parsers:
            return self._parsers[host]
        url = f"{scheme}://{host}/robots.txt"
        parser: RobotFileParser | None = None
        try:
            response = await self._client.get(url, timeout=10)
            if response.status_code < 400:
                parser = RobotFileParser()
                parser.parse(response.text.splitlines())
                delay = parser.crawl_delay(self._user_agent)
                if delay:
                    self._delays[host] = float(delay)
            # A 404 means "no rules", which RobotFileParser expresses by having
            # no entries at all; keep None to avoid lookups.
        except Exception as exc:
            logger.info("robots.txt unavailable host=%s error=%s", host, exc)
        self._parsers[host] = parser
        self._fetched_at[host] = time.monotonic()
        return parser

    async def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        if not parts.scheme or not parts.netloc:
            return False
        parser = await self._load(parts.scheme, parts.netloc)
        if parser is None:
            return True
        try:
            return parser.can_fetch(self._user_agent, url)
        except Exception:  # pragma: no cover - malformed rules
            return True

    def delay_for(self, url: str) -> float:
        return self._delays.get(urlsplit(url).netloc, 0.0)

    async def wait(self, url: str, base_delay: float) -> None:
        await asyncio.sleep(max(base_delay, self.delay_for(url)))
