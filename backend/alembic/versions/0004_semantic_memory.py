"""semantic memory: memories table, chat_sessions extraction watermark

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-05
"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_sessions",
        sa.Column(
            "memory_extracted_through_message_id",
            sa.Integer(),
            sa.ForeignKey("chat_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    op.create_table(
        "memories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column("memory_type", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "source_session_id", sa.Integer(), sa.ForeignKey("chat_sessions.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("access_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_accessed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.CheckConstraint("memory_type IN ('topic','struggle','preference','goal')", name="ck_memory_type"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_memory_confidence_range"),
    )
    op.create_index("memories_course_idx", "memories", ["course_id"])
    op.execute("CREATE INDEX memories_embedding_hnsw ON memories USING hnsw (embedding vector_cosine_ops)")


def downgrade() -> None:
    op.drop_table("memories")
    op.drop_column("chat_sessions", "memory_extracted_through_message_id")
