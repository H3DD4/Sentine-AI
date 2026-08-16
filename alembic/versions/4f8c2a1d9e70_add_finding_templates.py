"""add firm finding templates knowledge source

Revision ID: 4f8c2a1d9e70
Revises: a7b31c92d4e6
Create Date: 2026-08-14
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4f8c2a1d9e70"
down_revision: Union[str, Sequence[str], None] = "a7b31c92d4e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "finding_templates",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("template_code", sa.String(length=32), nullable=False),
        sa.Column("record_kind", sa.String(length=32), nullable=False),
        sa.Column("source_file", sa.String(length=512), nullable=False),
        sa.Column("source_file_hash", sa.String(length=64), nullable=False),
        sa.Column("source_table_index", sa.Integer(), nullable=False),
        sa.Column("section", sa.String(length=256), nullable=True),
        sa.Column("category", sa.String(length=256), nullable=True),
        sa.Column("topic", sa.String(length=512), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("iso_references", sa.JSON(), nullable=False),
        sa.Column("observations", sa.Text(), nullable=False),
        sa.Column("evidence_template", sa.Text(), nullable=True),
        sa.Column("affected_elements", sa.Text(), nullable=True),
        sa.Column("impact", sa.Text(), nullable=True),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column("implementation_complexity", sa.String(length=128), nullable=True),
        sa.Column("implementation_priority", sa.String(length=128), nullable=True),
        sa.Column("risk_assessments", sa.JSON(), nullable=False),
        sa.Column("raw_fields", sa.JSON(), nullable=False),
        sa.Column("parse_warnings", sa.JSON(), nullable=False),
        sa.Column("parser_version", sa.String(length=32), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("embed_model", sa.String(length=128), nullable=True),
        sa.Column("qdrant_synced_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "template_code", "record_kind", "source_file", "category",
        "content_hash", "qdrant_synced_at",
    ):
        op.create_index(f"ix_finding_templates_{column}", "finding_templates", [column])


def downgrade() -> None:
    op.drop_table("finding_templates")
