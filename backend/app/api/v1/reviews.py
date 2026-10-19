from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.chat import FeedbackCaseOut
from app.schemas.review import ReviewIn, ReviewOut, ReviewQueuePage, ReviewSummary
from app.services import chat_service, kb_service, review_service

router = APIRouter(tags=["reviews"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.get("/kbs/{kb_id}/reviews/queue", response_model=ReviewQueuePage)
async def list_review_queue(
    kb_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
    status_filter: Literal["unreviewed", "reviewed", "all"] = Query(
        default="unreviewed", alias="status"
    ),
    verdict: Literal["pass", "needs_fix", "fail"] | None = None,
    q: str | None = Query(default=None, max_length=200),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> ReviewQueuePage:
    await kb_service.get_owned_kb(db, kb_id, current_user.id)
    return await review_service.list_queue(
        db,
        kb_id,
        current_user.id,
        status=status_filter,
        verdict=verdict,
        q=q,
        offset=offset,
        limit=limit,
    )


@router.get("/kbs/{kb_id}/reviews/summary", response_model=ReviewSummary)
async def review_summary(
    kb_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> ReviewSummary:
    await kb_service.get_owned_kb(db, kb_id, current_user.id)
    return await review_service.summary(db, kb_id, current_user.id)


@router.put("/messages/{message_id}/review", response_model=ReviewOut)
async def upsert_review(
    message_id: UUID,
    payload: ReviewIn,
    db: DbSession,
    current_user: CurrentUser,
) -> ReviewOut:
    message = await chat_service.get_owned_message(db, message_id, current_user.id)
    review = await review_service.upsert_review(db, message, current_user.id, payload)
    await db.commit()
    return ReviewOut.model_validate(review)


@router.delete("/messages/{message_id}/review", status_code=status.HTTP_204_NO_CONTENT)
async def delete_review(
    message_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> None:
    await chat_service.get_owned_message(db, message_id, current_user.id)
    await review_service.delete_review(db, message_id)
    await db.commit()


@router.post(
    "/reviews/{review_id}/eval-case",
    response_model=FeedbackCaseOut,
    status_code=status.HTTP_201_CREATED,
)
async def convert_review_to_case(
    review_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> FeedbackCaseOut:
    review = await review_service.get_owned_review(db, review_id, current_user.id)
    dataset, case, created = await review_service.convert_review_to_case(
        db, review, current_user.id
    )
    await db.commit()
    return FeedbackCaseOut(
        dataset_id=dataset.id,
        dataset_name=dataset.name,
        case_id=case.id,
        created=created,
    )
