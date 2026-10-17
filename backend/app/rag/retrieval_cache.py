"""Redis cache for retrieval outcomes.

Key = KB + index_version + retrieval config + rewritten query + history
signature. Including ``index_version`` means ingestion invalidates the cache
without an explicit purge: the next version simply misses and writes new keys
that expire on their own.

The cache stores full candidates (including chunk text), so a hit skips query
embedding, vector search, BM25 and reranking. Redis failures are fail-open:
a cache outage must not take retrieval down.
"""

import hashlib
import json
import logging
from dataclasses import asdict
from typing import Any
from uuid import UUID

from app.db.redis import get_redis_client

logger = logging.getLogger(__name__)

CACHE_VERSION = 1
CACHE_PREFIX = "ragcache"


def make_key(
    kb_id: UUID,
    index_version: int,
    config: Any,
    query: str,
    history: list[tuple[str, str]],
) -> str:
    history_signature = hashlib.sha256(
        "\n".join(f"{role}:{content}" for role, content in history).encode("utf-8")
    ).hexdigest()
    payload = {
        "v": CACHE_VERSION,
        "kb": str(kb_id),
        "index_version": index_version,
        "config": asdict(config),
        "query": query,
        "history": history_signature,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return f"{CACHE_PREFIX}:v{CACHE_VERSION}:{kb_id}:{index_version}:{digest}"


async def get(key: str) -> dict[str, Any] | None:
    try:
        raw = await get_redis_client().get(key)
    except Exception as exc:  # fail-open
        logger.warning("retrieval cache read failed: %s", exc)
        return None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or "candidates" not in payload:
        return None
    return payload


async def set(key: str, payload: dict[str, Any], ttl_seconds: int) -> None:
    try:
        await get_redis_client().set(
            key,
            json.dumps(payload, ensure_ascii=False),
            ex=max(ttl_seconds, 1),
        )
    except Exception as exc:  # fail-open
        logger.warning("retrieval cache write failed: %s", exc)
