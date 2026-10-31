"""External data sources and their sync history.

The knowledge base is no longer only "files a human uploaded". A ``DataSource``
describes *where* documents come from and *how often* to look again; a
``SyncRun`` records one attempt with its per-outcome counters.

Two deliberate choices:

* Config is JSONB rather than a table per connector. Connector settings differ
  wildly (a crawl depth vs. a git ref), and a schema migration for every new
  connector would slow down exactly the part that should be cheap to extend.
  Validation lives in Pydantic schemas at the API boundary.
* ``documents.source_id`` is nullable and ``ON DELETE SET NULL``: deleting a
  source must not delete the knowledge it already contributed. The documents
  stay, they simply stop being "owned" by a connector.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

SOURCE_TYPES = ("web", "sitemap", "rss", "github")


class DataSource(Base):
    __tablename__ = "data_sources"
    __table_args__ = (Index("ix_data_sources_due", "enabled", "next_run_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kb_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    source_type: Mapped[str] = mapped_column(String(20))
    config: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    # 0 means "manual only"; anything else is a fixed interval in minutes.
    schedule_minutes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str] = mapped_column(String(20), default="idle", server_default="idle")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("data_sources.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="running", server_default="running")
    trigger: Mapped[str] = mapped_column(String(20), default="manual", server_default="manual")
    stats: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
