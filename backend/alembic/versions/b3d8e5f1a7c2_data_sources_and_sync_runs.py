"""data sources, sync runs and document origin

Revision ID: b3d8e5f1a7c2
Revises: a9e4b7c1d2f8
Create Date: 2026-10-31 20:55:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b3d8e5f1a7c2"
down_revision: str | Sequence[str] | None = "a9e4b7c1d2f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "data_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kb_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("schedule_minutes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(length=20), server_default="idle", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["kb_id"],
            ["knowledge_bases.id"],
            name=op.f("fk_data_sources_kb_id_knowledge_bases"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_sources")),
    )
    op.create_index(op.f("ix_data_sources_kb_id"), "data_sources", ["kb_id"], unique=False)
    op.create_index(
        "ix_data_sources_due", "data_sources", ["enabled", "next_run_at"], unique=False
    )

    op.create_table(
        "sync_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="running", nullable=False),
        sa.Column("trigger", sa.String(length=20), server_default="manual", nullable=False),
        sa.Column(
            "stats",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["data_sources.id"],
            name=op.f("fk_sync_runs_source_id_data_sources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sync_runs")),
    )
    op.create_index(op.f("ix_sync_runs_source_id"), "sync_runs", ["source_id"], unique=False)

    op.add_column(
        "documents", sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column("documents", sa.Column("external_url", sa.String(length=1000), nullable=True))
    op.create_foreign_key(
        op.f("fk_documents_source_id_data_sources"),
        "documents",
        "data_sources",
        ["source_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_documents_source_id"), "documents", ["source_id"], unique=False)
    op.create_index(
        "ix_documents_kb_external_url", "documents", ["kb_id", "external_url"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_documents_kb_external_url", table_name="documents")
    op.drop_index(op.f("ix_documents_source_id"), table_name="documents")
    op.drop_constraint(
        op.f("fk_documents_source_id_data_sources"), "documents", type_="foreignkey"
    )
    op.drop_column("documents", "external_url")
    op.drop_column("documents", "source_id")

    op.drop_index(op.f("ix_sync_runs_source_id"), table_name="sync_runs")
    op.drop_table("sync_runs")

    op.drop_index("ix_data_sources_due", table_name="data_sources")
    op.drop_index(op.f("ix_data_sources_kb_id"), table_name="data_sources")
    op.drop_table("data_sources")
