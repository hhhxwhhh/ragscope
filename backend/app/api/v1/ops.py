from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core import metrics
from app.core.deps import get_current_user
from app.models.user import User

router = APIRouter(prefix="/ops", tags=["ops"])

CurrentUser = Annotated[User, Depends(get_current_user)]


class HttpStats(BaseModel):
    requests_total: int
    errors_total: int
    error_rate: float
    p50_seconds: float | None
    p95_seconds: float | None
    p99_seconds: float | None


class OpsSnapshot(BaseModel):
    http: HttpStats
    retrieval: dict[str, int]
    llm: dict[str, int]
    agent: dict[str, int]
    ingest: dict[str, int]
    graphrag: dict[str, int]
    semantic_cache: dict[str, int]
    eval: dict[str, int]
    tasks: dict[str, Any]


@router.get("/summary", response_model=OpsSnapshot)
async def ops_summary(current_user: CurrentUser) -> OpsSnapshot:
    """JSON view of the process-local Prometheus registry.

    This is a convenience endpoint for the in-app ops panel. It is intentionally
    process-local: behind multiple replicas each instance reports its own
    counters, so the aggregated view belongs in Prometheus, not here.
    """
    snapshot = metrics.snapshot()
    from app.workers.dispatch import queue_depth, task_backend_info

    snapshot["tasks"] = {**task_backend_info(), "queue_depth": await queue_depth()}
    return OpsSnapshot(**snapshot)
