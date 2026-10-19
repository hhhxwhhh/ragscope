"""Human answer-review queue and quality summary.

Reviews are deliberately separate from user feedback: a reviewer audits a
sample of answers, records a verdict plus closed-vocabulary issue tags, and can
turn a failed answer into a regression case for the eval set.
"""

import logging
from collections import Counter
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.errors import BadRequestError, NotFoundError
from app.models.citation import Citation
from app.models.conversation import Conversation
from app.models.evaluation import EvalCase, EvalDataset
from app.models.knowledge_base import KnowledgeBase
from app.models.message import Message, MessageRole, MessageStatus
from app.models.review import ISSUE_TAGS, AnswerReview
from app.schemas.chat import CitationOut
from app.schemas.review import (
    ReviewIn,
    ReviewOut,
    ReviewQueueItem,
    ReviewQueuePage,
    ReviewSummary,
)
from app.services import chat_service, feedback_service

logger = logging.getLogger(__name__)

REVIEW_DATASET_NAME = "人工抽检回归集"
REVIEW_DATASET_DESCRIPTION = "由人工抽检不通过的回答生成；引用来源作为 chunk 级标注。"

_answer = aliased(Message, name="answer_message")
_question_message = aliased(Message, name="question_message")


def _question_subquery():
    """Fetch the user question immediately preceding an assistant answer."""
    return (
        select(_question_message.content)
        .where(
            _question_message.conversation_id == _answer.conversation_id,
            _question_message.role == MessageRole.USER,
            _question_message.created_at <= _answer.created_at,
        )
        .order_by(_question_message.created_at.desc(), _question_message.id.desc())
        .limit(1)
        .correlate(_answer)
        .scalar_subquery()
    )


def _scoped_conditions(kb_id: UUID, user_id: UUID) -> list:
    return [
        Conversation.kb_id == kb_id,
        Conversation.user_id == user_id,
        KnowledgeBase.owner_id == user_id,
        _answer.role == MessageRole.ASSISTANT,
        _answer.status.in_((MessageStatus.COMPLETED, MessageStatus.CANCELLED)),
    ]


async def list_queue(
    db: AsyncSession,
    kb_id: UUID,
    user_id: UUID,
    *,
    status: str,
    verdict: str | None,
    q: str | None,
    offset: int,
    limit: int,
) -> ReviewQueuePage:
    question = _question_subquery()
    conditions = _scoped_conditions(kb_id, user_id)
    if status == "unreviewed":
        conditions.append(AnswerReview.id.is_(None))
    elif status == "reviewed":
        conditions.append(AnswerReview.id.is_not(None))
    if verdict:
        conditions.append(AnswerReview.verdict == verdict)
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        conditions.append(or_(_answer.content.ilike(pattern), question.ilike(pattern)))

    rows = (
        await db.execute(
            select(_answer, Conversation.title, question.label("question"), AnswerReview)
            .join(Conversation, _answer.conversation_id == Conversation.id)
            .join(KnowledgeBase, Conversation.kb_id == KnowledgeBase.id)
            .outerjoin(AnswerReview, AnswerReview.message_id == _answer.id)
            .where(*conditions)
            .order_by(_answer.created_at.desc(), _answer.id.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()

    total = await db.scalar(
        select(func.count(_answer.id))
        .select_from(_answer)
        .join(Conversation, _answer.conversation_id == Conversation.id)
        .join(KnowledgeBase, Conversation.kb_id == KnowledgeBase.id)
        .outerjoin(AnswerReview, AnswerReview.message_id == _answer.id)
        .where(*conditions)
    )

    citations_by_message: dict[UUID, list[Citation]] = {}
    if rows:
        citations = (
            await db.scalars(
                select(Citation)
                .where(Citation.message_id.in_([row[0].id for row in rows]))
                .order_by(Citation.message_id, Citation.rank)
            )
        ).all()
        for citation in citations:
            citations_by_message.setdefault(citation.message_id, []).append(citation)

    items = [
        ReviewQueueItem(
            message_id=answer.id,
            conversation_id=answer.conversation_id,
            conversation_title=title,
            question=question_text or "",
            answer=answer.content,
            message_status=answer.status,
            prompt_variant=answer.prompt_variant,
            latency_ms=answer.latency_ms,
            citation_stats=answer.citation_stats,
            citations=[
                CitationOut.model_validate(citation)
                for citation in citations_by_message.get(answer.id, [])
            ],
            review=ReviewOut.model_validate(review) if review is not None else None,
            created_at=answer.created_at,
        )
        for answer, title, question_text, review in rows
    ]
    return ReviewQueuePage(
        items=items,
        total=int(total or 0),
        offset=offset,
        limit=limit,
    )


async def summary(db: AsyncSession, kb_id: UUID, user_id: UUID) -> ReviewSummary:
    total_answers = await db.scalar(
        select(func.count(_answer.id))
        .select_from(_answer)
        .join(Conversation, _answer.conversation_id == Conversation.id)
        .join(KnowledgeBase, Conversation.kb_id == KnowledgeBase.id)
        .where(*_scoped_conditions(kb_id, user_id))
    )

    rows = (
        await db.execute(
            select(AnswerReview.verdict, AnswerReview.issue_tags)
            .join(Message, AnswerReview.message_id == Message.id)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .join(KnowledgeBase, Conversation.kb_id == KnowledgeBase.id)
            .where(
                Conversation.kb_id == kb_id,
                Conversation.user_id == user_id,
                KnowledgeBase.owner_id == user_id,
                Message.role == MessageRole.ASSISTANT,
                Message.status.in_((MessageStatus.COMPLETED, MessageStatus.CANCELLED)),
            )
        )
    ).all()

    verdicts = Counter(row[0] for row in rows)
    issues: Counter[str] = Counter()
    for _, tags in rows:
        issues.update(tags or [])

    reviewed = len(rows)
    total = int(total_answers or 0)
    pass_count = verdicts.get("pass", 0)
    return ReviewSummary(
        total_answers=total,
        reviewed=reviewed,
        unreviewed=max(total - reviewed, 0),
        coverage=round(reviewed / total, 4) if total else 0.0,
        pass_count=pass_count,
        needs_fix_count=verdicts.get("needs_fix", 0),
        fail_count=verdicts.get("fail", 0),
        pass_rate=round(pass_count / reviewed, 4) if reviewed else 0.0,
        verdict_counts={
            "pass": pass_count,
            "needs_fix": verdicts.get("needs_fix", 0),
            "fail": verdicts.get("fail", 0),
        },
        issue_counts={tag: issues.get(tag, 0) for tag in ISSUE_TAGS},
    )


async def upsert_review(
    db: AsyncSession,
    message: Message,
    reviewer_id: UUID,
    payload: ReviewIn,
) -> AnswerReview:
    if message.role != MessageRole.ASSISTANT:
        raise BadRequestError("只能抽检助手回答")
    if message.status not in {MessageStatus.COMPLETED, MessageStatus.CANCELLED}:
        raise BadRequestError("只能抽检已生成完成的回答")

    review = await db.scalar(
        select(AnswerReview).where(AnswerReview.message_id == message.id)
    )
    tags = list(payload.issue_tags) if payload.verdict != "pass" else []
    if review is None:
        review = AnswerReview(
            message_id=message.id,
            reviewer_id=reviewer_id,
            verdict=payload.verdict,
            issue_tags=tags,
            note=payload.note,
        )
        db.add(review)
    else:
        review.verdict = payload.verdict
        review.issue_tags = tags
        review.note = payload.note
    await db.flush()
    # updated_at is server-generated; refresh before serialization so Pydantic
    # never triggers an implicit lazy load outside the async greenlet.
    await db.refresh(review)
    logger.info(
        "answer reviewed message=%s verdict=%s tags=%s",
        message.id,
        payload.verdict,
        tags,
    )
    return review


async def delete_review(db: AsyncSession, message_id: UUID) -> None:
    review = await db.scalar(
        select(AnswerReview).where(AnswerReview.message_id == message_id)
    )
    if review is not None:
        await db.delete(review)


async def get_owned_review(
    db: AsyncSession,
    review_id: UUID,
    user_id: UUID,
) -> AnswerReview:
    review = await db.scalar(
        select(AnswerReview)
        .join(Message, AnswerReview.message_id == Message.id)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .join(KnowledgeBase, Conversation.kb_id == KnowledgeBase.id)
        .where(
            AnswerReview.id == review_id,
            Conversation.user_id == user_id,
            KnowledgeBase.owner_id == user_id,
        )
    )
    if review is None:
        raise NotFoundError("抽检记录不存在")
    return review


async def convert_review_to_case(
    db: AsyncSession,
    review: AnswerReview,
    user_id: UUID,
) -> tuple[EvalDataset, EvalCase, bool]:
    message = await chat_service.get_owned_message(db, review.message_id, user_id)
    tags = "、".join(review.issue_tags or [])
    note = f"人工抽检 {review.verdict}：{tags}"[:1000]
    return await feedback_service.convert_to_eval_case(
        db,
        message,
        dataset_name=REVIEW_DATASET_NAME,
        dataset_description=REVIEW_DATASET_DESCRIPTION,
        notes=note,
    )
