"""add generated report history

Revision ID: 8d2e3f4a5b6c
Revises: f8574180196e
Create Date: 2026-08-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8d2e3f4a5b6c"
down_revision: Union[str, Sequence[str], None] = "f8574180196e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "generated_reports",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("client_name", sa.String(), nullable=False),
        sa.Column("engagement_title", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("storage_path", sa.String(), nullable=False),
        sa.Column("finding_snapshot", sa.JSON(), nullable=False),
        sa.Column("draft_snapshot", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_generated_reports_created_at", "generated_reports", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_generated_reports_created_at", table_name="generated_reports")
    op.drop_table("generated_reports")
