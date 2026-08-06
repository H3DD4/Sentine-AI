"""add report templates and structured finding fields

Revision ID: e5b92f71a3c4
Revises: d4a81c39f072
Create Date: 2026-08-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5b92f71a3c4"
down_revision: Union[str, Sequence[str], None] = "d4a81c39f072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("findings", sa.Column("affected_scope", sa.Text(), nullable=True))
    op.add_column("findings", sa.Column("technical_evidence", sa.Text(), nullable=True))
    op.add_column(
        "findings",
        sa.Column(
            "reproduction_steps",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )
    op.alter_column("findings", "reproduction_steps", server_default=None)
    op.add_column("findings", sa.Column("impact", sa.Text(), nullable=True))
    op.add_column("findings", sa.Column("severity", sa.String(), nullable=True))
    op.add_column("findings", sa.Column("cvss_score", sa.Float(), nullable=True))
    op.add_column("findings", sa.Column("cvss_vector", sa.String(), nullable=True))
    op.create_table(
        "report_templates",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("storage_path", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("report_templates")
    for column in (
        "cvss_vector", "cvss_score", "severity", "impact", "reproduction_steps",
        "technical_evidence", "affected_scope",
    ):
        op.drop_column("findings", column)
