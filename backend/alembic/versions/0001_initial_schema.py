"""Initial schema: sessions, documents, page_tags, metrics, red_flags.

Revision ID: 0001_initial_schema
Revises: 
Create Date: 2026-10-22

Tables created
──────────────
  sessions     – research sessions grouping documents
  documents    – uploaded PDFs with pipeline status tracking
  page_tags    – per-page tagging metadata (populated by Task 5)
  metrics      – extracted financial metrics (placeholder, Task 8+)
  red_flags    – detected red-flag findings (placeholder, Task 9+)
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── document_status enum (IF NOT EXISTS) ─────────────────────────────────
    # Use raw SQL so we never fail if a previous partial run already created it.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'document_status') THEN
                CREATE TYPE document_status AS ENUM (
                    'uploaded', 'parsing', 'tagging', 'chunking',
                    'indexing', 'ready', 'failed'
                );
            END IF;
        END
        $$;
        """
    )

    # ── sessions ──────────────────────────────────────────────────────────────
    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sessions"),
    )

    # ── documents ─────────────────────────────────────────────────────────────
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False),
        sa.Column("company", sa.String(255), nullable=True),
        sa.Column("fiscal_year", sa.Integer, nullable=True),
        sa.Column("doc_type", sa.String(100), nullable=True),
        sa.Column(
            "status",
            # postgresql.ENUM with create_type=False suppresses the
            # before_create event entirely – sa.Enum does not honour it.
            postgresql.ENUM(
                "uploaded",
                "parsing",
                "tagging",
                "chunking",
                "indexing",
                "ready",
                "failed",
                name="document_status",
                create_type=False,
            ),
            nullable=False,
            server_default="uploaded",
        ),
        sa.Column("page_count", sa.Integer, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name="fk_documents_session_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
        sa.UniqueConstraint("storage_key", name="uq_documents_storage_key"),
    )
    op.create_index("ix_documents_session_id", "documents", ["session_id"])

    # ── page_tags ─────────────────────────────────────────────────────────────
    op.create_table(
        "page_tags",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pdf_page", sa.Integer, nullable=False),
        sa.Column("printed_page", sa.Integer, nullable=True),
        sa.Column("section", sa.String(255), nullable=True),
        sa.Column("scope", sa.String(100), nullable=True),
        sa.Column("unit", sa.String(50), nullable=True),
        sa.Column("content_type", sa.String(100), nullable=True),
        sa.Column(
            "is_image_only",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_page_tags_document_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_page_tags"),
    )
    op.create_index("ix_page_tags_document_id", "page_tags", ["document_id"])

    # ── metrics (placeholder) ─────────────────────────────────────────────────
    op.create_table(
        "metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("value", sa.Numeric(20, 4), nullable=True),
        sa.Column("unit", sa.String(50), nullable=True),
        sa.Column("year", sa.Integer, nullable=True),
        sa.Column("source_page", sa.Integer, nullable=True),
        sa.Column("scope", sa.String(100), nullable=True),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_metrics_document_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_metrics"),
    )
    op.create_index("ix_metrics_document_id", "metrics", ["document_id"])

    # ── red_flags (placeholder) ───────────────────────────────────────────────
    op.create_table(
        "red_flags",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(100), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("source_page", sa.Integer, nullable=True),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_red_flags_document_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_red_flags"),
    )
    op.create_index("ix_red_flags_document_id", "red_flags", ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_red_flags_document_id", table_name="red_flags")
    op.drop_table("red_flags")

    op.drop_index("ix_metrics_document_id", table_name="metrics")
    op.drop_table("metrics")

    op.drop_index("ix_page_tags_document_id", table_name="page_tags")
    op.drop_table("page_tags")

    op.drop_index("ix_documents_session_id", table_name="documents")
    op.drop_table("documents")

    op.drop_table("sessions")

    # drop the enum type last
    sa.Enum(name="document_status").drop(op.get_bind(), checkfirst=True)
