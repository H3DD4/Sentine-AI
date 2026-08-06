"""add OWASP Top 10 source table

Revision ID: c9f14d60e2a1
Revises: 8d2e3f4a5b6c
Create Date: 2026-08-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9f14d60e2a1"
down_revision: Union[str, Sequence[str], None] = "8d2e3f4a5b6c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "owasp_top10",
        sa.Column("category_id", sa.String(length=16), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("overview", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("prevention", sa.Text(), nullable=True),
        sa.Column("scenarios", sa.Text(), nullable=True),
        sa.Column("full_text", sa.Text(), nullable=False),
        sa.Column("cwe_ids", sa.JSON(), nullable=False),
        sa.Column("ref_urls", sa.JSON(), nullable=False),
        sa.Column("source_url", sa.String(length=1024), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("embed_model", sa.String(length=128), nullable=True),
        sa.Column("qdrant_synced_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("category_id"),
    )
    op.create_index("ix_owasp_top10_rank", "owasp_top10", ["rank"])
    op.create_index("ix_owasp_top10_year", "owasp_top10", ["year"])
    op.create_index("ix_owasp_top10_content_hash", "owasp_top10", ["content_hash"])
    op.create_index("ix_owasp_top10_qdrant_synced_at", "owasp_top10", ["qdrant_synced_at"])


def downgrade() -> None:
    op.drop_table("owasp_top10")
