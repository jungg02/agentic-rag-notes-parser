"""message_figures: persist which figures were surfaced for a message

Fixes related_figures only existing in the one-time SSE "done" payload
and never being refetchable -- switching courses (or refreshing) remounts
ChatPane and loses them for every already-rendered message, even though
citations survive the same remount via message_citations.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-06
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "message_figures",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("figure_id", sa.Integer(), sa.ForeignKey("figures.id", ondelete="CASCADE"), nullable=False),
    )
    op.create_index("message_figures_message_idx", "message_figures", ["message_id"])


def downgrade() -> None:
    op.drop_table("message_figures")
