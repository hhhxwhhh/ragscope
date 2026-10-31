"""Knowledge graph endpoints: build, inspect, clear.

Building calls an LLM once per chunk, so it is a background job with a Redis
single-flight flag rather than a request-scoped task: a second click while a build
is running returns 409 instead of doubling the token bill. The flag is
deliberately short-lived (1 hour) — if a worker dies, the next attempt is allowed
rather than leaving the knowledge base permanently locked.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.redis import get_redis_client
from app.db.session import SessionLocal, get_db
from app.models.knowledge_base import KnowledgeBase
from app.models.user import User
from app.services import graph_service, kb_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kbs/{kb_id}/graph", tags=["graph"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]

BUILD_TTL_SECONDS = 3600


def _build_key(kb_id: UUID) -> str:
    return f"graph:building:{kb_id}"


async def _run_build(kb_id: UUID) -> None:
    """Background entry point; owns its session so the request can return."""
    redis = get_redis_client()
    async with SessionLocal() as db:
        try:
            kb = await db.get(KnowledgeBase, kb_id)
            if kb is None:
                return
            stats = await graph_service.build_for_kb(db, kb)
            logger.info(
                "graph build finished kb=%s documents=%s chunks=%s entities=%s relations=%s "
                "failed=%s",
                kb_id,
                stats.documents,
                stats.chunks,
                stats.entities,
                stats.relations,
                stats.failed_chunks,
            )
        except Exception:
            await db.rollback()
            logger.exception("graph build failed kb=%s", kb_id)
        finally:
            try:
                await redis.delete(_build_key(kb_id))
            except Exception:  # pragma: no cover - best effort cleanup
                logger.warning("failed to clear graph build flag kb=%s", kb_id)


@router.get("")
async def get_graph(
    kb_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> dict:
    kb = await kb_service.get_owned_kb(db, kb_id, current_user.id)
    payload = await graph_service.get_graph(db, kb)
    payload["stats"] = await graph_service.stats(db, kb.id)
    payload["building"] = bool(await get_redis_client().get(_build_key(kb.id)))
    return payload


@router.get("/stats")
async def graph_stats(
    kb_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> dict:
    kb = await kb_service.get_owned_kb(db, kb_id, current_user.id)
    stats = await graph_service.stats(db, kb.id)
    stats["building"] = bool(await get_redis_client().get(_build_key(kb.id)))
    return stats


@router.post("/build", status_code=status.HTTP_202_ACCEPTED)
async def build_graph(
    kb_id: UUID,
    background: BackgroundTasks,
    db: DbSession,
    current_user: CurrentUser,
) -> dict:
    kb = await kb_service.get_owned_kb(db, kb_id, current_user.id)
    redis = get_redis_client()
    # SET NX is the single-flight guard; no read-then-write race.
    acquired = await redis.set(_build_key(kb.id), "1", ex=BUILD_TTL_SECONDS, nx=True)
    if not acquired:
        return {"status": "already_building", "kb_id": str(kb.id)}
    background.add_task(_run_build, kb.id)
    return {"status": "building", "kb_id": str(kb.id)}


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def clear_graph(
    kb_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> None:
    kb = await kb_service.get_owned_kb(db, kb_id, current_user.id)
    await graph_service.clear_graph(db, kb.id)
