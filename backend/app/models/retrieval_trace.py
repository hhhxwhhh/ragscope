import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RetrievalTrace(Base):
    """Per-answer record of every retrieval stage, for the debug panel and eval."""

    __tablename__ = "retrieval_traces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kb_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    query: Mapped[str] = mapped_column(Text)
    rewritten_query: Mapped[str] = mapped_column(Text)
    rewrite_applied: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # LLM-generated retrieval angles (empty unless multi-query was enabled).
    sub_queries: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    reranked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    rerank_error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    candidates: Mapped[dict[str, Any]] = mapped_column(JSONB)
    stage_timings: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
