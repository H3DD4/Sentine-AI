"""add broader official OWASP documents

Revision ID: d4a81c39f072
Revises: c9f14d60e2a1
Create Date: 2026-08-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4a81c39f072"
down_revision: Union[str, Sequence[str], None] = "c9f14d60e2a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "owasp_documents",
        sa.Column("document_id", sa.String(length=64), nullable=False),
        sa.Column("project", sa.String(length=32), nullable=False),
        sa.Column("repository", sa.String(length=128), nullable=False),
        sa.Column("branch", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=True),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("git_sha", sa.String(length=40), nullable=False),
        sa.Column("source_url", sa.String(length=1024), nullable=False),
        sa.Column("cwe_ids", sa.JSON(), nullable=False),
        sa.Column("ref_urls", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("embed_model", sa.String(length=128), nullable=True),
        sa.Column("qdrant_synced_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("document_id"),
    )
    op.create_index("ix_owasp_documents_project", "owasp_documents", ["project"])
    op.create_index("ix_owasp_documents_version", "owasp_documents", ["version"])
    op.create_index(
        "ix_owasp_documents_project_path",
        "owasp_documents",
        ["project", "path"],
        unique=True,
    )
    op.create_index("ix_owasp_documents_content_hash", "owasp_documents", ["content_hash"])
    op.create_index(
        "ix_owasp_documents_qdrant_synced_at",
        "owasp_documents",
        ["qdrant_synced_at"],
    )


def downgrade() -> None:
    op.drop_table("owasp_documents")
