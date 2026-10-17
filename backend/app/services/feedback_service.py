"""Answer feedback and the production-to-eval feedback loop.

A thumbs-down is useful only if it changes something. ``convert_to_eval_case``
turns the question behind a bad answer into a labelled regression case, using
the citations the answer actually relied on as chunk-level labels. The next
retrieval run then tells you whether the fix worked.
"""

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, ConflictError
from app.models.citation import Citation
from app.models.conversation import Conversation
from app.models.evaluation import EvalCase, EvalDataset
from app.models.feedback import MessageFeedback
from app.models.knowledge_base import KnowledgeBase
from app.models.message import Message, MessageRole, MessageStatus
from app.schemas.chat import FeedbackIn
from app.schemas.eval import EvalCaseCreate, RelevantRef
from app.services import chat_service, eval_service

logger = logging.getLogger(__name__)

FEEDBACK_DATASET_NAME = "用户反馈回归集"
FEEDBACK_DATASET_DESCRIPTION = "由回答差评一键生成的回归用例；引用来源作为 chunk 级标注。"


async def upsert_feedback(
    db: AsyncSession,
    message: Message,
    user_id: UUID,
    payload: FeedbackIn,
) -> MessageFeedback:
    if message.role != MessageRole.ASSISTANT:
        raise BadRequestError("只能对助手回答进行评价")
    if message.status not in {MessageStatus.COMPLETED, MessageStatus.CANCELLED}:
        raise BadRequestError("只能评价已生成完成的回答")

    feedback = await db.scalar(
        select(MessageFeedback).where(MessageFeedback.message_id == message.id)
    )
    if feedback is None:
        feedback = MessageFeedback(
            message_id=message.id,
            user_id=user_id,
            rating=payload.rating,
            reason=payload.reason,
            comment=payload.comment,
        )
        db.add(feedback)
    else:
        feedback.rating = payload.rating
        feedback.reason = payload.reason
        feedback.comment = payload.comment
    await db.flush()
    # updated_at is server-generated on insert; refresh so serialization never
    # triggers an implicit lazy load outside the greenlet context.
    await db.refresh(feedback)
    return feedback


async def delete_feedback(db: AsyncSession, message_id: UUID) -> None:
    feedback = await db.scalar(
        select(MessageFeedback).where(MessageFeedback.message_id == message_id)
    )
    if feedback is not None:
        await db.delete(feedback)


async def _ensure_feedback_dataset(db: AsyncSession, kb: KnowledgeBase) -> EvalDataset:
    dataset = await db.scalar(
        select(EvalDataset).where(
            EvalDataset.kb_id == kb.id,
            EvalDataset.name == FEEDBACK_DATASET_NAME,
        )
    )
    if dataset is not None:
        if (dataset.chunk_size, dataset.chunk_overlap) != (kb.chunk_size, kb.chunk_overlap):
            raise ConflictError("知识库切块参数已变更，反馈回归集已失效；请新建数据集")
        return dataset
    return await eval_service.create_dataset(
        db, kb, FEEDBACK_DATASET_NAME, FEEDBACK_DATASET_DESCRIPTION
    )


async def convert_to_eval_case(
    db: AsyncSession,
    message: Message,
) -> tuple[EvalDataset, EvalCase, bool]:
    """Create (or reuse) a labelled case from an assistant answer.

    Returns ``(dataset, case, created)``. Citations that the answer marked as
    ``cited`` become relevant refs; a refusal / uncited answer becomes an
    unanswerable case.
    """
    if message.role != MessageRole.ASSISTANT:
        raise BadRequestError("只能把助手回答转换为评测用例")

    conversation = await db.get(Conversation, message.conversation_id)
    if conversation is None:
        raise BadRequestError("会话不存在")
    question_message = await chat_service.get_previous_user_message(db, message)
    if question_message is None:
        raise BadRequestError("找不到对应的用户问题")
    kb = await db.get(KnowledgeBase, conversation.kb_id)
    if kb is None:
        raise BadRequestError("知识库不存在")

    dataset = await _ensure_feedback_dataset(db, kb)
    existing = await db.scalar(
        select(EvalCase).where(
            EvalCase.dataset_id == dataset.id,
            EvalCase.question == question_message.content,
        )
    )
    if existing is not None:
        return dataset, existing, False

    citations = (
        await db.scalars(
            select(Citation)
            .where(Citation.message_id == message.id)
            .order_by(Citation.rank)
        )
    ).all()
    cited = [citation for citation in citations if citation.cited]
    source = cited or citations
    refs = [
        RelevantRef(
            document_id=citation.document_id,
            chunk_index=citation.chunk_index,
            filename=citation.filename,
        )
        for citation in source
        if citation.document_id is not None
    ]
    answerable = bool(refs)
    notes = "来自用户反馈的差评" if answerable else "来自用户反馈：原回答未给出可引用来源"

    payload = EvalCaseCreate(
        question=question_message.content,
        relevant_refs=refs,
        answerable=answerable,
        notes=notes[:1000],
    )
    case = await eval_service.create_case(db, dataset, payload)
    logger.info("feedback case created dataset=%s message=%s", dataset.id, message.id)
    return dataset, case, True
