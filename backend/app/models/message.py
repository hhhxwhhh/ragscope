import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MessageRole:
    USER = "user"
    ASSISTANT = "assistant"


class MessageStatus:
    STREAMING = "streaming"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation_created", "conversation_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text, default="", server_default="")
    status: Mapped[str] = mapped_column(String(20), default="completed", server_default="completed")
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Marker/source counts computed server-side, e.g.
    # {"markers": 4, "invalid_markers": 0, "cited_sources": 3, "citation_rate": 0.6}
    citation_stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    rewrite_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Which grounded-answer prompt produced this message. Null on user turns and
    # on old rows; the UI treats it as "grounded" when absent.
    prompt_variant: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
