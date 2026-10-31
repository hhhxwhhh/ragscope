"""Celery tasks.

Async SQLAlchemy pools are bound to the event loop that created their connections.
``asyncio.run`` gives each task a *new* loop, so the process-wide engine must be
disposed at the end of every task — otherwise the second task in the same worker
reuses a connection from a loop that no longer exists and fails with
"attached to a different loop". Disposing is cheap (connections are re-opened
lazily) and it also closes the Redis client, which has the same constraint.

The alternative — running a single long-lived loop inside the worker — hides the
problem until someone adds a thread pool. Explicit disposal is easier to reason
about and is visible in one place.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from app.core.env import configure_env

configure_env()

from app.db.redis import close_redis  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.services import eval_service  # noqa: E402
from app.services.ingestion import recover_stale_documents  # noqa: E402
from app.workers.celery_app import celery_app  # noqa: E402
from app.workers.ingestion import run_ingestion  # noqa: E402

logger = logging.getLogger(__name__)


async def _run_isolated(
    func: Callable[..., Awaitable[Any]], *args: Any
) -> Any:
    try:
        return await func(*args)
    finally:
        await close_redis()
        await engine.dispose()


@celery_app.task(name="ragscope.ingest", bind=True, max_retries=2)
def ingest_document_task(self, document_id: str) -> dict[str, Any]:
    """Parse, chunk, embed and index one document."""
    try:
        asyncio.run(_run_isolated(run_ingestion, UUID(document_id)))
        return {"document_id": document_id, "status": "done"}
    except Exception as exc:  # pragma: no cover - worker level safety net
        logger.exception("ingest task failed document=%s", document_id)
        # A retry is only useful for transient failures (DB/Redis blips); the
        # ingestion service already recorded a permanent failure for parse
        # errors, and a third attempt would just repeat it.
        raise self.retry(exc=exc, countdown=10) from exc


@celery_app.task(name="ragscope.eval", bind=True, max_retries=0)
def run_evaluation_task(self, run_id: str) -> dict[str, Any]:
    asyncio.run(_run_isolated(eval_service.execute_run, UUID(run_id)))
    return {"run_id": run_id, "status": "done"}


@celery_app.task(name="ragscope.recover_stale")
def recover_stale_task() -> dict[str, int]:
    """Mark documents and runs abandoned by a dead worker as failed."""

    async def _recover() -> dict[str, int]:
        async with SessionLocal() as db:
            documents = await recover_stale_documents(db)
        async with SessionLocal() as db:
            runs = await eval_service.recover_stale_runs(db)
        return {"documents": documents, "runs": runs}

    return asyncio.run(_run_isolated(_recover))
