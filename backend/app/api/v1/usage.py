"""Token usage and budget API."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.services import usage_service

router = APIRouter(prefix="/usage", tags=["usage"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.get("/summary")
async def usage_summary(
    db: DbSession,
    current_user: CurrentUser,
    days: Annotated[int, Query(ge=1, le=90)] = 14,
) -> dict:
    """Daily token series, top knowledge bases and today's budget state."""
    return await usage_service.summary(db, current_user.id, days)
