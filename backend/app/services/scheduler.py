"""In-process scheduler for `TASK_BACKEND=inline`.

Celery deployments get scheduling from beat; a laptop or a single-container demo
does not have beat, so without this the "sync every 30 minutes" promise silently
never happens. The loop is intentionally dumb: wake up, ask the service which
sources are due, let it dispatch. All the interesting logic (due query, single
flight, dispatch) already lives in ``source_service``.

Two safety properties:

* the tick is guarded by a Redis lock inside ``sync_due_sources``, so running
  several uvicorn workers does not multiply the work;
* the loop never raises — a failed tick must not kill the scheduler task.
"""

from __future__ import annotations

import asyncio
import logging

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None


async def _loop(interval: int) -> None:
    from app.services import source_service

    while True:
        await asyncio.sleep(interval)
        try:
            dispatched = await source_service.sync_due_sources()
            if dispatched:
                logger.info("scheduler dispatched %s sync runs", dispatched)
        except asyncio.CancelledError:  # pragma: no cover - shutdown path
            raise
        except Exception:
            logger.exception("scheduler tick failed")


def start() -> bool:
    """Start the loop when it is the right backend for the job."""
    global _task
    settings = get_settings()
    if not settings.sync_enabled:
        return False
    if (settings.task_backend or "inline").lower() == "celery":
        # Beat owns scheduling in this deployment; two schedulers would race.
        return False
    if _task is not None and not _task.done():
        return False
    _task = asyncio.create_task(_loop(max(15, settings.sync_scheduler_interval_seconds)))
    logger.info(
        "inline scheduler started interval=%ss",
        settings.sync_scheduler_interval_seconds,
    )
    return True


async def stop() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    finally:
        _task = None
