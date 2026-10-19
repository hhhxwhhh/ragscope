import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ReviewVerdict:
    PASS = "pass"
    NEEDS_FIX = "needs_fix"
    FAIL = "fail"


VERDICTS = (ReviewVerdict.PASS, ReviewVerdict.NEEDS_FIX, ReviewVerdict.FAIL)

# Keep the vocabulary closed: free-form tags make the summary counts unusable.
ISSUE_TAGS = (
    "unsupported_claim",
    "wrong_citation",
    "incomplete",
    "off_topic",
    "refusal_error",
    "formatting",
)


class AnswerReview(Base):
    """One human quality review per assistant message.

    Feedback is the end user's signal; a review is an internal audit record.
    Keeping them separate means a passing answer can still be thumbs-downed by
    a user, and a failed review can be converted into a regression case without
    inventing a fake negative rating.
    """

    __tablename__ = "answer_reviews"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_answer_reviews_message"),
        CheckConstraint(
            "verdict IN ('pass', 'needs_fix', 'fail')",
            name="ck_answer_reviews_verdict",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    reviewer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    verdict: Mapped[str] = mapped_column(String(20))
    issue_tags: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
