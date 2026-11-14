"""Tiered indexing: store page text, tier and indexed flag on page_tags.

Revision ID: 0002_page_text_and_tier
Revises: 0001_initial_schema
Create Date: 2026-10-22
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_page_text_and_tier"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "page_tags",
        sa.Column("tier", sa.Integer, nullable=False, server_default="2"),
    )
    op.add_column("page_tags", sa.Column("text", sa.Text, nullable=True))
    op.add_column(
        "page_tags",
        sa.Column("indexed", sa.Boolean, nullable=False, server_default=sa.text("false")),
    )
    # One row per (document, pdf_page) so re-runs can safely replace rows.
    op.create_unique_constraint(
        "uq_page_tags_document_page", "page_tags", ["document_id", "pdf_page"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_page_tags_document_page", "page_tags", type_="unique")
    op.drop_column("page_tags", "indexed")
    op.drop_column("page_tags", "text")
    op.drop_column("page_tags", "tier")
