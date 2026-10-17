from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.chat import FeedbackCaseOut, FeedbackIn, FeedbackOut
from app.services import chat_service, feedback_service

router = APIRouter(tags=["feedback"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.put("/messages/{message_id}/feedback", response_model=FeedbackOut)
async def upsert_feedback(
    message_id: UUID,
    payload: FeedbackIn,
    db: DbSession,
    current_user: CurrentUser,
) -> FeedbackOut:
    message = await chat_service.get_owned_message(db, message_id, current_user.id)
    feedback = await feedback_service.upsert_feedback(db, message, current_user.id, payload)
    await db.commit()
    return FeedbackOut.model_validate(feedback)


@router.delete("/messages/{message_id}/feedback", status_code=status.HTTP_204_NO_CONTENT)
async def delete_feedback(
    message_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> None:
    await chat_service.get_owned_message(db, message_id, current_user.id)
    await feedback_service.delete_feedback(db, message_id)
    await db.commit()


@router.post(
    "/messages/{message_id}/feedback/eval-case",
    response_model=FeedbackCaseOut,
    status_code=status.HTTP_201_CREATED,
)
async def convert_feedback_to_case(
    message_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> FeedbackCaseOut:
    message = await chat_service.get_owned_message(db, message_id, current_user.id)
    dataset, case, created = await feedback_service.convert_to_eval_case(db, message)
    await db.commit()
    return FeedbackCaseOut(
        dataset_id=dataset.id,
        dataset_name=dataset.name,
        case_id=case.id,
        created=created,
    )
