"""figures: extracted-image table for cross-modal retrieval (Phase 4)

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-05
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from pgvector.sqlalchemy import Vector

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "figures",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("image_path", sa.Text(), nullable=False),
        sa.Column("bbox", JSONB(), nullable=False),
        sa.Column("embedding", Vector(768), nullable=False),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column(
            "caption_tsv",
            TSVECTOR(),
            sa.Computed("to_tsvector('english', coalesce(caption, ''))", persisted=True),
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("figures_course_idx", "figures", ["course_id"])
    op.execute("CREATE INDEX figures_embedding_hnsw ON figures USING hnsw (embedding vector_cosine_ops)")
    op.execute("CREATE INDEX figures_caption_tsv_gin ON figures USING gin (caption_tsv)")


def downgrade() -> None:
    op.drop_table("figures")
