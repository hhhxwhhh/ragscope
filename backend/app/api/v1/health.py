import logging
from typing import Annotated

import anyio
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db
from app.rag import vector_store

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])
settings = get_settings()


@router.get("/healthz", summary="Liveness probe")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", summary="Readiness probe")
async def readyz(
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    checks: dict[str, str] = {}

    try:
        await db.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:  # pragma: no cover - depends on environment
        checks["postgres"] = f"error: {type(exc).__name__}"

    client = None
    try:
        client = aioredis.from_url(
            settings.redis_url,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        await client.ping()
        checks["redis"] = "ok"
    except Exception as exc:  # pragma: no cover - depends on environment
        checks["redis"] = f"error: {type(exc).__name__}"
    finally:
        if client is not None:
            await client.aclose()

    try:
        await anyio.to_thread.run_sync(vector_store.ping)
        checks["chroma"] = "ok"
    except Exception as exc:  # pragma: no cover - depends on environment
        logger.warning("chroma readiness check failed: %s", exc)
        checks["chroma"] = f"error: {type(exc).__name__}"

    healthy = all(value == "ok" for value in checks.values())
    if not healthy:
        response.status_code = 503

    return {"status": "ok" if healthy else "degraded", "checks": checks}
