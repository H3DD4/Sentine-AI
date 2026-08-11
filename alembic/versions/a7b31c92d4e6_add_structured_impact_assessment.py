"""add structured impact assessment to findings

Revision ID: a7b31c92d4e6
Revises: 2e11397c963b
Create Date: 2026-08-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7b31c92d4e6"
down_revision: Union[str, Sequence[str], None] = "2e11397c963b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("findings", sa.Column("impact_assessment", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("findings", "impact_assessment")
