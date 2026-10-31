"""Semantic answer cache entries.

Deliberately stores the *evidence set* alongside the answer: a cached answer is
only meaningful together with the chunks its ``[n]`` markers point at. See
``app/rag/semantic_cache.py`` for the lookup/staleness rules.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SemanticCacheEntry(Base):
    __tablename__ = "semantic_cache_entries"
    __table_args__ = (
        UniqueConstraint(
            "kb_id",
            "index_version",
            "prompt_variant",
            "normalized_question",
            name="uq_semantic_cache_key",
        ),
        Index("ix_semantic_cache_lookup", "kb_id", "index_version", "prompt_variant"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kb_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    index_version: Mapped[int] = mapped_column(Integer)
    prompt_variant: Mapped[str] = mapped_column(String(32), default="grounded")
    question: Mapped[str] = mapped_column(Text)
    normalized_question: Mapped[str] = mapped_column(String(500))
    answer: Mapped[str] = mapped_column(Text)
    candidates: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    trace: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    usage: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    embedding: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    hits: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_hit_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
