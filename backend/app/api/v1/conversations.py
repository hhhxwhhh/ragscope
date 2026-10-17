from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.chat import ConversationCreate, ConversationOut, MessageOut
from app.schemas.common import Page
from app.services import chat_service, kb_service

router = APIRouter(tags=["conversations"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]
Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=100)]


@router.get("/kbs/{kb_id}/conversations", response_model=Page[ConversationOut])
async def list_conversations(
    kb_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
    offset: Offset = 0,
    limit: Limit = 20,
) -> Page[ConversationOut]:
    await kb_service.get_owned_kb(db, kb_id, current_user.id)
    items, total = await chat_service.list_conversations(db, kb_id, current_user.id, offset, limit)
    return Page[ConversationOut](
        items=[ConversationOut.model_validate(item) for item in items],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/kbs/{kb_id}/conversations",
    response_model=ConversationOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    kb_id: UUID,
    payload: ConversationCreate,
    db: DbSession,
    current_user: CurrentUser,
) -> ConversationOut:
    await kb_service.get_owned_kb(db, kb_id, current_user.id)
    conversation = await chat_service.create_conversation(db, kb_id, current_user.id, payload.title)
    await db.commit()
    return ConversationOut.model_validate(conversation)


@router.get("/conversations/{conversation_id}/messages", response_model=Page[MessageOut])
async def list_messages(
    conversation_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
    offset: Offset = 0,
    limit: Limit = 100,
) -> Page[MessageOut]:
    await chat_service.get_owned_conversation(db, conversation_id, current_user.id)
    bundles, total = await chat_service.list_messages(db, conversation_id, offset, limit)
    return Page[MessageOut](
        items=[chat_service.to_message_out(bundle) for bundle in bundles],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> None:
    conversation = await chat_service.get_owned_conversation(db, conversation_id, current_user.id)
    await chat_service.delete_conversation(db, conversation)
    await db.commit()
