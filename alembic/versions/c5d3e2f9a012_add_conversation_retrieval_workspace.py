"""add conversation retrieval workspace

Revision ID: c5d3e2f9a012
Revises: 4f8c2a1d9e70
"""

from alembic import op
import sqlalchemy as sa


revision = "c5d3e2f9a012"
down_revision = "4f8c2a1d9e70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "analysis_conversations",
        sa.Column("retrieval_workspace", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("analysis_conversations", "retrieval_workspace")
