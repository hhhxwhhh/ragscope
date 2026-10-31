"""Connector contract shared by every source type.

A fetcher's only job is: given a JSON config, return normalised documents and a
list of non-fatal errors. It must not touch the database, the vector store or the
LLM — that separation is what lets the sync service own idempotency and lets each
connector be tested with plain fixture strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SourceItem:
    """One document candidate produced by a connector."""

    external_id: str
    title: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def filename(self) -> str:
        """Stable, filesystem-safe name used when the item is persisted."""
        stem = self.title or self.external_id
        safe = "".join(ch if ch.isalnum() or ch in "-_ ." else "_" for ch in stem)
        safe = " ".join(safe.split())[:80].strip(" ._") or "untitled"
        return f"{safe}.md"


@dataclass
class FetchOutcome:
    items: list[SourceItem] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    pages_fetched: int = 0

    def add_error(self, url: str, message: str) -> None:
        # Keep the first N errors: a dead site can produce hundreds of identical
        # failures and the run record is meant to be readable.
        if len(self.errors) < 20:
            self.errors.append({"url": url[:500], "error": message[:300]})


class FetchError(RuntimeError):
    """Configuration or transport problem that should fail the whole run."""


class Fetcher:
    source_type = "base"

    async def fetch(self, config: dict[str, Any]) -> FetchOutcome:  # pragma: no cover
        raise NotImplementedError
