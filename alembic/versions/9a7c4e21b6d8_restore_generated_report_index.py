"""restore generated report history index

Revision ID: 9a7c4e21b6d8
Revises: 567586ed2c93
Create Date: 2026-08-10
"""

from typing import Sequence, Union

from alembic import op


revision: str = "9a7c4e21b6d8"
down_revision: Union[str, Sequence[str], None] = "567586ed2c93"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_generated_reports_created_at",
        "generated_reports",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_generated_reports_created_at", table_name="generated_reports")
