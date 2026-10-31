"""Data source lifecycle and incremental synchronisation.

The core loop is deliberately boring, because "keep a knowledge base fresh" is a
correctness problem far more than a cleverness problem::

    fetch -> normalise -> hash -> compare with the stored hash
          -> unchanged?  skip (no embedding, no token cost)
          -> changed?    rewrite the file, drop old chunks/vectors, re-ingest
          -> new?        create the document and ingest

Three properties matter and each has a test:

1. **Idempotent**: running sync twice in a row produces ``unchanged`` the second
   time. The document identity is ``(kb_id, external_url)``; the freshness check
   is the SHA-256 of the normalised text.
2. **Attributable**: every document remembers which source produced it, and every
   attempt is a ``SyncRun`` row with per-outcome counters, so "why did my
   knowledge base change overnight" is answerable.
3. **Bounded**: ``max_pages`` per source and ``sync_max_items_per_run`` per run.
   A scheduled crawler without a ceiling is a self-inflicted incident.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.db.redis import get_redis_client
from app.db.session import SessionLocal
from app.models.document import Document, DocumentStatus
from app.models.knowledge_base import KnowledgeBase
from app.models.source import DataSource, SyncRun
from app.schemas.source import SourceCreate, SourceUpdate, validate_config
from app.services import document_service, storage
from app.services.ingestion import ingest_document
from app.services.sources import SourceItem, fetch

logger = logging.getLogger(__name__)

RUNNING = "running"
SUCCESS = "success"
PARTIAL = "partial"
FAILED = "failed"


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def compute_next_run(
    schedule_minutes: int, now: datetime | None = None
) -> datetime | None:
    """Next due time; ``None`` means manual-only."""
    if not schedule_minutes or schedule_minutes <= 0:
        return None
    base = now or datetime.now(UTC)
    return base + timedelta(minutes=schedule_minutes)


def decide_action(existing_digest: str | None, new_digest: str) -> str:
    """Classify one fetched item against what is already stored."""
    if existing_digest is None:
        return "create"
    return "unchanged" if existing_digest == new_digest else "update"


def content_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _truncate(text: str) -> str:
    limit = get_settings().sync_max_document_chars
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n…（原文过长，已截断）\n"


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
async def list_sources(db: AsyncSession, kb_id: UUID) -> list[DataSource]:
    rows = await db.scalars(
        select(DataSource)
        .where(DataSource.kb_id == kb_id)
        .order_by(DataSource.created_at.asc())
    )
    return list(rows)


async def get_owned_source(db: AsyncSession, source_id: UUID, owner_id: UUID) -> DataSource:
    source = await db.scalar(
        select(DataSource)
        .join(KnowledgeBase, DataSource.kb_id == KnowledgeBase.id)
        .where(DataSource.id == source_id, KnowledgeBase.owner_id == owner_id)
    )
    if source is None:
        raise NotFoundError("数据源不存在")
    return source


async def create_source(
    db: AsyncSession, kb: KnowledgeBase, payload: SourceCreate
) -> DataSource:
    now = datetime.now(UTC)
    source = DataSource(
        kb_id=kb.id,
        name=payload.name,
        source_type=payload.source_type,
        config=payload.config,
        schedule_minutes=payload.schedule_minutes,
        enabled=payload.enabled,
        next_run_at=compute_next_run(payload.schedule_minutes, now),
    )
    db.add(source)
    await db.flush()
    await db.refresh(source)
    return source


async def update_source(
    db: AsyncSession, source: DataSource, payload: SourceUpdate
) -> DataSource:
    data = payload.model_dump(exclude_unset=True)
    if data.get("config") is not None:
        try:
            data["config"] = validate_config(source.source_type, data["config"])
        except ValueError as exc:
            raise BadRequestError(str(exc)) from exc
    for key, value in data.items():
        setattr(source, key, value)
    if "schedule_minutes" in data or "enabled" in data:
        source.next_run_at = (
            compute_next_run(source.schedule_minutes)
            if source.enabled
            else None
        )
    await db.flush()
    await db.refresh(source)
    return source


async def delete_source(db: AsyncSession, source: DataSource) -> None:
    """Delete the connector, keep the documents it contributed."""
    await db.delete(source)
    await db.flush()


async def list_runs(db: AsyncSession, source_id: UUID, limit: int = 20) -> list[SyncRun]:
    rows = await db.scalars(
        select(SyncRun)
        .where(SyncRun.source_id == source_id)
        .order_by(SyncRun.started_at.desc())
        .limit(limit)
    )
    return list(rows)


async def count_pending_runs(db: AsyncSession, source_id: UUID) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(SyncRun)
            .where(SyncRun.source_id == source_id, SyncRun.status == RUNNING)
        )
        or 0
    )


# --------------------------------------------------------------------------- #
# Running a sync
# --------------------------------------------------------------------------- #
async def create_run(
    db: AsyncSession, source: DataSource, trigger: str = "manual"
) -> SyncRun:
    if await count_pending_runs(db, source.id):
        raise ConflictError("该数据源已有同步任务在运行")
    run = SyncRun(source_id=source.id, status=RUNNING, trigger=trigger, stats={})
    db.add(run)
    source.last_status = RUNNING
    await db.flush()
    await db.refresh(run)
    return run


async def execute_run(run_id: UUID) -> None:
    """Background entry point. Owns its session; safe to run in Celery or inline."""
    async with SessionLocal() as db:
        run = await db.get(SyncRun, run_id)
        if run is None or run.status != RUNNING:
            return
        source = await db.get(DataSource, run.source_id)
        if source is None:
            run.status = FAILED
            run.error = "数据源已被删除"
            run.finished_at = datetime.now(UTC)
            await db.commit()
            return

        kb = await db.get(KnowledgeBase, source.kb_id)
        if kb is None:
            run.status = FAILED
            run.error = "知识库已被删除"
            run.finished_at = datetime.now(UTC)
            await db.commit()
            return

        started = datetime.now(UTC)
        try:
            outcome = await fetch(source.source_type, source.config or {})
            stats = await _apply_items(db, kb, source, outcome.items)
            run.stats = {
                **stats,
                "pages_fetched": outcome.pages_fetched,
                "errors": outcome.errors,
            }
            # Partial when the connector hit individual page failures but the
            # run itself completed; success only when every fetch worked.
            run.status = PARTIAL if outcome.errors else SUCCESS
        except Exception as exc:
            await db.rollback()
            logger.exception("sync failed source=%s", source.id)
            run = await db.get(SyncRun, run_id)
            if run is not None:
                run.status = FAILED
                run.error = f"{exc.__class__.__name__}: {exc}"[:2000]
                run.finished_at = datetime.now(UTC)
            source = await db.get(DataSource, run.source_id) if run else None
            if source is not None:
                source.last_status = FAILED
                source.last_error = (run.error or "")[:2000] if run else None
                source.last_run_at = datetime.now(UTC)
                source.next_run_at = compute_next_run(source.schedule_minutes)
            await db.commit()
            return

        finished = datetime.now(UTC)
        run.finished_at = finished
        run.stats = {**run.stats, "duration_ms": int((finished - started).total_seconds() * 1000)}
        source.last_run_at = finished
        source.last_status = run.status
        source.last_error = None
        source.next_run_at = compute_next_run(source.schedule_minutes, finished)
        await db.commit()
        logger.info(
            "sync done source=%s status=%s stats=%s",
            source.id,
            run.status,
            run.stats,
        )


async def _apply_items(
    db: AsyncSession,
    kb: KnowledgeBase,
    source: DataSource,
    items: list[SourceItem],
) -> dict[str, int]:
    settings = get_settings()
    stats = {"added": 0, "updated": 0, "unchanged": 0, "failed": 0, "skipped": 0}
    limit = max(1, settings.sync_max_items_per_run)

    for item in items[:limit]:
        text = _truncate((item.content or "").strip())
        if len(text) < 40:
            stats["skipped"] += 1
            continue
        digest = content_digest(text)

        existing = await db.scalar(
            select(Document).where(
                Document.kb_id == kb.id, Document.external_url == item.external_id
            )
        )
        action = decide_action(existing.sha256 if existing else None, digest)
        if action == "unchanged":
            stats["unchanged"] += 1
            continue

        try:
            if existing is not None:
                await _update_document(db, kb, source, existing, item, text, digest)
                stats["updated"] += 1
            else:
                await _create_document(db, kb, source, item, text, digest)
                stats["added"] += 1
        except IntegrityError:
            # Same bytes already exist under a different URL in this KB.
            await db.rollback()
            stats["unchanged"] += 1
        except Exception as exc:
            await db.rollback()
            stats["failed"] += 1
            logger.warning(
                "sync item failed source=%s url=%s error=%s",
                source.id,
                item.external_id,
                exc,
            )

    return stats


async def _create_document(
    db: AsyncSession,
    kb: KnowledgeBase,
    source: DataSource,
    item: SourceItem,
    text: str,
    digest: str,
) -> None:
    document_id = uuid4()
    path, size, sha = storage.save_text(kb.id, document_id, item.filename(), text)
    document = Document(
        id=document_id,
        kb_id=kb.id,
        filename=item.filename(),
        ext=".md",
        size_bytes=size,
        sha256=sha,
        storage_path=str(path),
        status=DocumentStatus.PENDING,
        source_id=source.id,
        external_url=item.external_id[:1000],
    )
    db.add(document)
    await db.commit()
    await ingest_document(db, document.id)


async def _update_document(
    db: AsyncSession,
    kb: KnowledgeBase,
    source: DataSource,
    document: Document,
    item: SourceItem,
    text: str,
    digest: str,
) -> None:
    path, size, sha = storage.save_text(kb.id, document.id, item.filename(), text)
    # Drops chunks and bumps index_version so BM25/vector caches invalidate
    # before the new content is embedded.
    await document_service.reset_for_retry(db, document)
    document.filename = item.filename()
    document.ext = ".md"
    document.size_bytes = size
    document.sha256 = sha
    document.storage_path = str(path)
    document.source_id = source.id
    document.external_url = item.external_id[:1000]
    await db.commit()
    await ingest_document(db, document.id)


# --------------------------------------------------------------------------- #
# Scheduling
# --------------------------------------------------------------------------- #
async def due_sources(db: AsyncSession, now: datetime | None = None) -> list[DataSource]:
    moment = now or datetime.now(UTC)
    rows = await db.scalars(
        select(DataSource)
        .where(
            DataSource.enabled.is_(True),
            DataSource.schedule_minutes > 0,
            (DataSource.next_run_at.is_(None)) | (DataSource.next_run_at <= moment),
        )
        .order_by(DataSource.next_run_at.asc().nulls_first())
        .limit(20)
    )
    return list(rows)


async def sync_due_sources() -> int:
    """Create a run for every due source and dispatch it.

    A Redis lock (60s) makes this safe when several API replicas run the inline
    scheduler at the same time: only one of them wins the tick.
    """
    settings = get_settings()
    if not settings.sync_enabled:
        return 0

    redis = get_redis_client()
    try:
        acquired = await redis.set("sync:tick", "1", ex=60, nx=True)
    except Exception:
        acquired = True
    if not acquired:
        return 0

    dispatched = 0
    async with SessionLocal() as db:
        sources = await due_sources(db)
        for source in sources:
            try:
                run = await create_run(db, source, trigger="schedule")
                await db.commit()
            except ConflictError:
                await db.rollback()
                continue
            except Exception:
                await db.rollback()
                logger.exception("failed to create scheduled run source=%s", source.id)
                continue
            dispatch_run(run.id)
            dispatched += 1
    return dispatched


def dispatch_run(run_id: UUID, background: Any | None = None) -> str:
    """Send a run to Celery, a FastAPI BackgroundTask, or a plain asyncio task."""
    settings = get_settings()
    if (settings.task_backend or "inline").lower() == "celery":
        try:
            from app.workers.celery_app import celery_app

            celery_app.send_task(
                "ragscope.sync_run", args=[str(run_id)], queue=settings.celery_queue
            )
            return "celery"
        except Exception:  # pragma: no cover - broker down
            logger.exception("celery dispatch failed, running sync inline")

    if background is not None:
        background.add_task(execute_run, run_id)
        return "inline"
    asyncio.create_task(execute_run(run_id))
    return "asyncio"


async def preview(source_type: str, config: dict[str, Any], limit: int = 5) -> dict[str, Any]:
    """Fetch a few items without persisting anything (used by the UI's test button)."""
    outcome = await fetch(
        source_type,
        {**config, "max_pages": limit, "max_items": limit, "max_files": limit},
    )
    return {
        "items": [
            {
                "title": item.title,
                "url": item.external_id,
                "chars": len(item.content),
                "preview": item.content[:280],
            }
            for item in outcome.items[:limit]
        ],
        "errors": outcome.errors[:5],
        "pages_fetched": outcome.pages_fetched,
    }

