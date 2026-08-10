"""add durable analysis conversations"""
from alembic import op
import sqlalchemy as sa

revision = "b4c2d1e8f901"
down_revision = "9a7c4e21b6d8"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "analysis_conversations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(), nullable=False, server_default="Untitled analysis"),
        sa.Column("messages", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("validation_snapshot", sa.JSON(), nullable=True),
        sa.Column("readiness_snapshot", sa.JSON(), nullable=True),
        sa.Column("finding_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_analysis_conversations_user_id", "analysis_conversations", ["user_id"])
    op.create_index("ix_analysis_conversations_updated_at", "analysis_conversations", ["updated_at"])
    op.create_index("ix_analysis_conversations_finding_id", "analysis_conversations", ["finding_id"])

def downgrade():
    op.drop_table("analysis_conversations")
