"""GitHub repository connector.

Docs live in git for a lot of teams: ``docs/`` in a product repo, an internal
handbook, a runbook repository. Instead of asking people to re-upload files, this
connector reads a path from a public (or token-authorised) repository and turns
Markdown/text files into knowledge base documents.

Implementation notes:

* The REST contents API returns base64 for files and an array for directories, so
  a recursive walk needs no `git clone` and no working copy.
* Unauthenticated access is limited to 60 requests/hour. That is enough for a
  small docs folder, so a token is optional — but the error message says exactly
  what to do when the limit is hit, instead of surfacing a bare 403.
* Only text-like extensions are accepted; binaries are skipped rather than
  embedded as garbage.
"""

from __future__ import annotations

import base64
import fnmatch
import logging
from typing import Any

import httpx

from app.core.config import get_settings
from app.services.sources.base import Fetcher, FetchOutcome, SourceItem

logger = logging.getLogger(__name__)

API_ROOT = "https://api.github.com"
TEXT_PATTERNS = ("*.md", "*.mdx", "*.txt", "*.rst", "*.adoc")
MAX_DEPTH = 3


class GithubFetcher(Fetcher):
    source_type = "github"

    async def fetch(self, config: dict[str, Any]) -> FetchOutcome:
        repo = str(config.get("repo") or "").strip().strip("/")
        if "/" not in repo:
            raise ValueError("github 数据源需要 repo，格式为 owner/name")

        path = str(config.get("path") or "").strip("/")
        ref = config.get("ref") or None
        patterns = tuple(config.get("glob") or TEXT_PATTERNS)
        max_files = min(int(config.get("max_files") or 30), 100)
        token = config.get("token") or get_settings().github_token

        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "RAGScopeBot/0.5",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        outcome = FetchOutcome()
        async with httpx.AsyncClient(timeout=30, headers=headers, follow_redirects=True) as client:
            await self._walk(client, repo, path, ref, patterns, max_files, outcome, depth=0)
        return outcome

    async def _walk(
        self,
        client: httpx.AsyncClient,
        repo: str,
        path: str,
        ref: str | None,
        patterns: tuple[str, ...],
        max_files: int,
        outcome: FetchOutcome,
        depth: int,
    ) -> None:
        if len(outcome.items) >= max_files or depth > MAX_DEPTH:
            return

        params = {"ref": ref} if ref else {}
        url = f"{API_ROOT}/repos/{repo}/contents/{path}".rstrip("/")
        try:
            response = await client.get(url, params=params)
        except Exception as exc:
            outcome.add_error(url, f"{exc.__class__.__name__}: {exc}")
            return

        if response.status_code == 403:
            outcome.add_error(
                url,
                "GitHub API 限流（未认证 60 次/小时）。配置 GITHUB_TOKEN 或降低同步频率。",
            )
            return
        if response.status_code == 404:
            outcome.add_error(url, "仓库或路径不存在（私有仓库需要 token）")
            return
        if response.status_code >= 400:
            outcome.add_error(url, f"HTTP {response.status_code}")
            return

        payload = response.json()
        entries = payload if isinstance(payload, list) else [payload]

        for entry in entries:
            if len(outcome.items) >= max_files:
                return
            entry_type = entry.get("type")
            entry_path = entry.get("path") or ""
            if entry_type == "dir":
                await self._walk(
                    client, repo, entry_path, ref, patterns, max_files, outcome, depth + 1
                )
                continue
            if entry_type != "file":
                continue

            name = entry.get("name") or entry_path
            if not any(fnmatch.fnmatch(name.lower(), pattern) for pattern in patterns):
                continue

            content = await self._read_file(client, entry, outcome)
            if not content:
                continue
            title = name.rsplit(".", 1)[0]
            outcome.items.append(
                SourceItem(
                    external_id=entry.get("html_url") or f"github://{repo}/{entry_path}",
                    title=title,
                    content=f"# {title}\n\n{content.strip()}\n",
                    metadata={
                        "repo": repo,
                        "path": entry_path,
                        "sha": entry.get("sha"),
                        "url": entry.get("html_url"),
                    },
                )
            )
            outcome.pages_fetched += 1

    async def _read_file(
        self, client: httpx.AsyncClient, entry: dict[str, Any], outcome: FetchOutcome
    ) -> str:
        encoded = entry.get("content")
        if encoded and entry.get("encoding") == "base64":
            try:
                return base64.b64decode(encoded).decode("utf-8", errors="replace")
            except Exception as exc:  # pragma: no cover - malformed payload
                outcome.add_error(entry.get("path") or "", f"base64 解码失败：{exc}")
                return ""
        download_url = entry.get("download_url")
        if not download_url:
            return ""
        try:
            response = await client.get(download_url)
            response.raise_for_status()
            return response.text
        except Exception as exc:
            outcome.add_error(download_url, f"下载失败：{exc.__class__.__name__}")
            return ""
