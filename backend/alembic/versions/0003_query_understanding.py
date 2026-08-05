"""query understanding: query_turns, retrieved_chunks, session compaction state

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-05
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_sessions", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column(
        "chat_sessions",
        sa.Column(
            "summarized_through_message_id",
            sa.Integer(),
            sa.ForeignKey("chat_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    op.create_table(
        "query_turns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "message_id", sa.Integer(), sa.ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("intent", sa.Text(), nullable=False),
        sa.Column("rewritten_query", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.CheckConstraint(
            "intent IN ('factual_lookup','comparison','summarization','follow_up')", name="ck_query_turn_intent"
        ),
        sa.UniqueConstraint("message_id", name="uq_query_turn_message"),
    )

    op.create_table(
        "retrieved_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "message_id", sa.Integer(), sa.ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("chunk_id", sa.Integer(), sa.ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.UniqueConstraint("message_id", "rank", name="uq_retrieved_chunk_message_rank"),
    )


def downgrade() -> None:
    op.drop_table("retrieved_chunks")
    op.drop_table("query_turns")
    op.drop_column("chat_sessions", "summarized_through_message_id")
    op.drop_column("chat_sessions", "summary")
