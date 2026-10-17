from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.dashboard import DashboardSummary, DashboardTrend
from app.services import dashboard_service

router = APIRouter(tags=["dashboard"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.get("/dashboard/summary", response_model=DashboardSummary)
async def get_summary(db: DbSession, current_user: CurrentUser) -> DashboardSummary:
    return await dashboard_service.summary(db, current_user.id)


@router.get("/dashboard/trend", response_model=DashboardTrend)
async def get_trend(
    db: DbSession,
    current_user: CurrentUser,
    days: Annotated[int, Query(ge=1, le=90)] = 14,
) -> DashboardTrend:
    return await dashboard_service.trend(db, current_user.id, days)
