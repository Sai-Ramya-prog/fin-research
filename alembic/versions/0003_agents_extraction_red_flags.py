"""Milestone 2: extraction + red-flag agent columns.

Revision ID: 0003_agents
Revises: 0002_page_text_and_tier
Create Date: 2026-11-02
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_agents"
down_revision: Union[str, None] = "0002_page_text_and_tier"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("extraction_status", sa.String(20), nullable=True))
    op.add_column("documents", sa.Column("red_flag_status", sa.String(20), nullable=True))
    op.add_column("documents", sa.Column("analysis_error", sa.Text, nullable=True))

    op.add_column("metrics", sa.Column("kind", sa.String(20), nullable=False, server_default="extracted"))
    op.add_column("metrics", sa.Column("source_text", sa.Text, nullable=True))
    op.add_column("metrics", sa.Column("formula", sa.String(255), nullable=True))
    op.add_column("metrics", sa.Column("status", sa.String(20), nullable=False, server_default="verified"))
    op.add_column("metrics", sa.Column("note", sa.Text, nullable=True))

    op.add_column("red_flags", sa.Column("severity", sa.String(10), nullable=False, server_default="Medium"))
    op.add_column("red_flags", sa.Column("title", sa.String(255), nullable=True))
    op.add_column("red_flags", sa.Column("evidence", sa.Text, nullable=True))


def downgrade() -> None:
    for col in ("evidence", "title", "severity"):
        op.drop_column("red_flags", col)
    for col in ("note", "status", "formula", "source_text", "kind"):
        op.drop_column("metrics", col)
    for col in ("analysis_error", "red_flag_status", "extraction_status"):
        op.drop_column("documents", col)
