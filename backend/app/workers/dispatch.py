"""Task dispatch: one call site, two execution backends.

Handlers call ``schedule(background, "ingest", document.id)`` and do not know
whether the work runs in-process or in a Celery worker. Keeping the decision in
one module means a deployment can switch backends with an environment variable and
no code change — and it is obvious where to look when a job "disappears".
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import BackgroundTasks

from app.core.config import get_settings

logger = logging.getLogger(__name__)

TASK_NAMES = {"ingest": "ragscope.ingest", "eval": "ragscope.eval"}


async def _run_ingest(task_id: UUID) -> None:
    from app.workers.ingestion import run_ingestion

    await run_ingestion(task_id)


async def _run_eval(task_id: UUID) -> None:
    from app.services.eval_service import execute_run

    await execute_run(task_id)


INLINE_RUNNERS = {"ingest": _run_ingest, "eval": _run_eval}


def schedule(background: BackgroundTasks, kind: str, task_id: UUID) -> str:
    """Queue a background task and return the backend actually used."""
    if kind not in TASK_NAMES:
        raise ValueError(f"unknown task kind: {kind}")

    settings = get_settings()
    backend = (settings.task_backend or "inline").lower()

    if backend == "celery":
        try:
            from app.workers.celery_app import celery_app

            celery_app.send_task(
                TASK_NAMES[kind],
                args=[str(task_id)],
                queue=settings.celery_queue,
            )
            return "celery"
        except Exception:
            # If the broker is unreachable the user's upload should still be
            # processed. Degrade to in-process work and leave a loud log line.
            logger.exception("celery dispatch failed, falling back to inline task=%s", kind)

    background.add_task(INLINE_RUNNERS[kind], task_id)
    return "inline"


async def queue_depth() -> int | None:
    """Pending messages on the Celery queue (Redis list length).

    Used by the ops panel. Returns ``None`` when Celery is not configured, so the
    UI can render "not enabled" instead of a misleading zero.
    """
    settings = get_settings()
    if (settings.task_backend or "inline").lower() != "celery":
        return None
    from app.db.redis import get_redis_client

    try:
        return int(await get_redis_client().llen(settings.celery_queue) or 0)
    except Exception:  # pragma: no cover - Redis down
        return None


def task_backend_info() -> dict[str, Any]:
    settings = get_settings()
    return {
        "backend": (settings.task_backend or "inline").lower(),
        "queue": settings.celery_queue,
        "inline_tasks": sorted(TASK_NAMES),
    }
