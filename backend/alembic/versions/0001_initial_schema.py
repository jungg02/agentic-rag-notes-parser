"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-07-04
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from pgvector.sqlalchemy import Vector

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "courses",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("name", sa.String, nullable=False, unique=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("course_id", sa.BigInteger, sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("original_filename", sa.Text, nullable=False),
        sa.Column("original_format", sa.Text, nullable=False),
        sa.Column("original_path", sa.Text, nullable=False),
        sa.Column("pdf_path", sa.Text),
        sa.Column("page_count", sa.Integer),
        sa.Column("ingest_status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("ingest_error", sa.Text),
        sa.Column("file_sha256", sa.Text, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("original_format IN ('pdf','docx','pptx')", name="ck_document_format"),
        sa.CheckConstraint(
            "ingest_status IN ('pending','converting','parsing','embedding','ready','failed')",
            name="ck_document_status",
        ),
        sa.UniqueConstraint("course_id", "file_sha256", name="uq_document_course_sha256"),
    )

    op.create_table(
        "chunks",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("document_id", sa.BigInteger, sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("course_id", sa.BigInteger, sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("context_header", sa.Text),
        sa.Column("page_number", sa.Integer, nullable=False),
        sa.Column("bboxes", JSONB, nullable=False),
        sa.Column("token_count", sa.Integer, nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_chunk_document_index"),
    )
    op.execute(
        "ALTER TABLE chunks ADD COLUMN tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', coalesce(context_header, '') || ' ' || text)) STORED"
    )
    op.execute("CREATE INDEX chunks_tsv_gin ON chunks USING GIN (tsv)")
    op.execute("CREATE INDEX chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops)")
    op.create_index("chunks_course_idx", "chunks", ["course_id"])
    op.create_index("chunks_document_idx", "chunks", ["document_id"])

    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("course_id", sa.BigInteger, sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.Text),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("session_id", sa.BigInteger, sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("role IN ('user','assistant')", name="ck_message_role"),
    )

    op.create_table(
        "message_citations",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("message_id", sa.BigInteger, sa.ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_id", sa.BigInteger, sa.ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("marker_index", sa.Integer, nullable=False),
        sa.UniqueConstraint("message_id", "marker_index", name="uq_citation_message_marker"),
    )


def downgrade() -> None:
    op.drop_table("message_citations")
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
    op.drop_table("chunks")
    op.drop_table("documents")
    op.drop_table("courses")
