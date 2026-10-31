"""Knowledge graph extracted from the corpus.

Three tables, deliberately normalised rather than a graph database:

* ``graph_entities`` — deduplicated by ``(kb_id, normalized_name)`` so "X1" and
  "x1" are one node, with a ``mention_count`` used as a cheap popularity prior.
* ``graph_relations`` — directed edges with a relation label and weight; the
  unique key makes re-extraction idempotent.
* ``chunk_entities`` — the join that makes graph traversal *grounded*: every
  entity points back to the chunks that mention it, so a graph hop can always
  return quotable text instead of an unverifiable assertion.

PostgreSQL is sufficient at this scale: a one-hop expansion is two indexed
lookups, and recursive CTEs cover longer paths if they are ever needed. A
dedicated graph store would add an operational dependency without changing the
query shape.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class GraphEntity(Base):
    __tablename__ = "graph_entities"
    __table_args__ = (
        UniqueConstraint("kb_id", "normalized_name", name="uq_graph_entities_kb_name"),
        Index("ix_graph_entities_kb_type", "kb_id", "entity_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kb_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    normalized_name: Mapped[str] = mapped_column(String(120))
    entity_type: Mapped[str] = mapped_column(
        String(24), default="concept", server_default="concept"
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    mention_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GraphRelation(Base):
    __tablename__ = "graph_relations"
    __table_args__ = (
        UniqueConstraint(
            "kb_id", "source_id", "target_id", "relation", name="uq_graph_relations_edge"
        ),
        Index("ix_graph_relations_kb_source", "kb_id", "source_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kb_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("graph_entities.id", ondelete="CASCADE"), index=True
    )
    target_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("graph_entities.id", ondelete="CASCADE"), index=True
    )
    relation: Mapped[str] = mapped_column(String(40))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    weight: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0")
    source_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chunks.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChunkEntity(Base):
    __tablename__ = "chunk_entities"
    __table_args__ = (UniqueConstraint("chunk_id", "entity_id", name="uq_chunk_entities_pair"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chunks.id", ondelete="CASCADE"), index=True
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("graph_entities.id", ondelete="CASCADE"), index=True
    )
    weight: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0")
