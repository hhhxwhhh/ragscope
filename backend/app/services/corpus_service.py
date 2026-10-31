"""Local corpus packs: browse crawled Markdown and import it into a knowledge base.

A *corpus pack* is a directory produced by ``scripts/crawl_corpus.py``:

```
data/corpus/vue3-zh/
├── index.json          # name, source, licence, per-document metadata
├── LICENSE-NOTICE.md
├── guide__introduction-3f2a1c.md
└── ...
```

Why keep this layer at all, when connectors already ingest into a KB directly?

* **Reproducibility** — the demo/eval corpus is a file in git. Rebuilding the same
  knowledge base does not depend on a live site or on someone's laptop.
* **Reuse** — one pack can seed several knowledge bases (different chunk sizes,
  different retrieval configs) for A/B comparisons.
* **Review before ingest** — a human can look at what was crawled *before* 50
  documents enter the index, which is the difference between a pipeline and a
  surprise.

Import is deliberately "snapshot" semantics: if a document with the same URL
already exists in the knowledge base, it is skipped. Freshness is the connector's
job; this path is for bootstrapping.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import BadRequestError, NotFoundError
from app.db.redis import get_redis_client
from app.db.session import SessionLocal
from app.models.document import Document, DocumentStatus
from app.models.knowledge_base import KnowledgeBase
from app.services import storage
from app.services.ingestion import ingest_document

logger = logging.getLogger(__name__)

INDEX_FILE = "index.json"
PROGRESS_TTL_SECONDS = 3600


def corpus_root() -> Path:
    return Path(get_settings().corpus_dir).resolve()


def _pack_dir(slug: str) -> Path:
    root = corpus_root()
    candidate = (root / slug).resolve()
    # A slug comes from the URL; never let it escape the corpus root.
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise NotFoundError("语料包不存在") from exc
    if not candidate.is_dir():
        raise NotFoundError("语料包不存在")
    return candidate


def _read_index(slug: str) -> dict[str, Any]:
    index_path = _pack_dir(slug) / INDEX_FILE
    if not index_path.is_file():
        raise NotFoundError("语料包缺少 index.json")
    try:
        return json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BadRequestError("index.json 解析失败") from exc


def list_packs() -> list[dict[str, Any]]:
    root = corpus_root()
    if not root.is_dir():
        return []
    packs: list[dict[str, Any]] = []
    for directory in sorted(root.iterdir()):
        if not directory.is_dir() or not (directory / INDEX_FILE).is_file():
            continue
        try:
            index = json.loads((directory / INDEX_FILE).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("skipping broken corpus pack %s", directory.name)
            continue
        packs.append(
            {
                "slug": directory.name,
                "name": index.get("name") or directory.name,
                "source": index.get("source") or "",
                "license": index.get("license") or "",
                "count": index.get("count") or len(index.get("documents") or []),
                "chars": index.get("chars") or 0,
                "generated_at": index.get("generated_at"),
            }
        )
    return packs


def get_pack(slug: str) -> dict[str, Any]:
    index = _read_index(slug)
    documents = index.get("documents") or []
    return {
        "slug": slug,
        "name": index.get("name") or slug,
        "source": index.get("source") or "",
        "license": index.get("license") or "",
        "generated_at": index.get("generated_at"),
        "count": len(documents),
        "chars": index.get("chars") or sum(int(item.get("chars") or 0) for item in documents),
        "errors": index.get("errors") or [],
        "documents": [
            {
                "title": item.get("title") or item.get("file"),
                "url": item.get("url"),
                "file": item.get("file"),
                "chars": item.get("chars") or 0,
            }
            for item in documents
        ],
    }


def read_document(slug: str, filename: str) -> str:
    pack = _pack_dir(slug)
    target = (pack / filename).resolve()
    try:
        target.relative_to(pack)
    except ValueError as exc:
        raise NotFoundError("文档不存在") from exc
    if not target.is_file():
        raise NotFoundError("文档不存在")
    return target.read_text(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# Import
# --------------------------------------------------------------------------- #
def _progress_key(kb_id: UUID, slug: str) -> str:
    return f"corpus:import:{kb_id}:{slug}"


async def _set_progress(kb_id: UUID, slug: str, payload: dict[str, Any]) -> None:
    try:
        redis = get_redis_client()
        await redis.hset(
            _progress_key(kb_id, slug),
            mapping={key: str(value) for key, value in payload.items()},
        )
        await redis.expire(_progress_key(kb_id, slug), PROGRESS_TTL_SECONDS)
    except Exception:  # pragma: no cover - progress is best effort
        logger.debug("failed to write corpus import progress", exc_info=True)


async def get_progress(kb_id: UUID, slug: str) -> dict[str, Any] | None:
    try:
        redis = get_redis_client()
        data = await redis.hgetall(_progress_key(kb_id, slug))
    except Exception:
        return None
    if not data:
        return None
    return {
        "total": int(data.get("total", 0)),
        "done": int(data.get("done", 0)),
        "added": int(data.get("added", 0)),
        "skipped": int(data.get("skipped", 0)),
        "failed": int(data.get("failed", 0)),
        "status": data.get("status", "running"),
        "error": data.get("error"),
    }


async def import_pack(kb_id: UUID, slug: str) -> None:
    """Background importer: one document at a time, progress in Redis."""
    pack = get_pack(slug)
    documents = pack["documents"]
    await _set_progress(
        kb_id,
        slug,
        {
            "total": len(documents),
            "done": 0,
            "added": 0,
            "skipped": 0,
            "failed": 0,
            "status": "running",
        },
    )

    async with SessionLocal() as db:
        kb = await db.get(KnowledgeBase, kb_id)
        if kb is None:
            await _set_progress(
                kb_id,
                slug,
                {
                    "total": len(documents),
                    "done": 0,
                    "status": "failed",
                    "error": "知识库不存在",
                },
            )
            return

        added = skipped = failed = done = 0
        for item in documents:
            done += 1
            try:
                filename = item["file"]
                text = read_document(slug, filename)
                existing = await db.scalar(
                    select(Document).where(
                        Document.kb_id == kb.id, Document.external_url == item["url"]
                    )
                )
                if existing is not None:
                    skipped += 1
                else:
                    await _create(db, kb, slug, item, text)
                    added += 1
            except Exception:
                await db.rollback()
                failed += 1
                logger.exception("corpus import failed slug=%s file=%s", slug, item.get("file"))
            await _set_progress(
                kb_id,
                slug,
                {
                    "total": len(documents),
                    "done": done,
                    "added": added,
                    "skipped": skipped,
                    "failed": failed,
                    "status": "running",
                },
            )

        await _set_progress(
            kb_id,
            slug,
            {
                "total": len(documents),
                "done": done,
                "added": added,
                "skipped": skipped,
                "failed": failed,
                "status": "completed",
            },
        )
        logger.info(
            "corpus import done slug=%s kb=%s added=%s skipped=%s failed=%s",
            slug,
            kb_id,
            added,
            skipped,
            failed,
        )


async def _create(
    db: AsyncSession,
    kb: KnowledgeBase,
    slug: str,
    item: dict[str, Any],
    text: str,
) -> None:
    document_id = uuid4()
    title = item.get("title") or document_id.hex[:8]
    safe_name = "".join(ch if ch.isalnum() or ch in "-_ ." else "_" for ch in title)[:80].strip()
    path, size, sha = storage.save_text(kb.id, document_id, f"{safe_name or 'doc'}.md", text)
    document = Document(
        id=document_id,
        kb_id=kb.id,
        filename=f"{safe_name or 'doc'}.md",
        ext=".md",
        size_bytes=size,
        sha256=sha,
        storage_path=str(path),
        status=DocumentStatus.PENDING,
        external_url=(item.get("url") or f"corpus://{slug}/{item.get('file')}")[:1000],
    )
    db.add(document)
    await db.commit()
    await ingest_document(db, document.id)
