from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

EMBEDDING_DIM = 384


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    documents: Mapped[list["Document"]] = relationship(back_populates="course", cascade="all, delete-orphan")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("course_id", "file_sha256", name="uq_document_course_sha256"),
        CheckConstraint("original_format IN ('pdf','docx','pptx')", name="ck_document_format"),
        CheckConstraint(
            "ingest_status IN ('pending','converting','parsing','embedding','ready','failed')",
            name="ck_document_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    original_format: Mapped[str] = mapped_column(Text, nullable=False)
    original_path: Mapped[str] = mapped_column(Text, nullable=False)
    pdf_path: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int | None] = mapped_column(Integer)
    ingest_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    ingest_error: Mapped[str | None] = mapped_column(Text)
    file_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    course: Mapped["Course"] = relationship(back_populates="documents")
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_chunk_document_index"),
        Index("chunks_course_idx", "course_id"),
        Index("chunks_document_idx", "document_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    context_header: Mapped[str | None] = mapped_column(Text)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    bboxes: Mapped[dict] = mapped_column(JSONB, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    is_ocr: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    tsv = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(context_header, '') || ' ' || text)", persisted=True),
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    document: Mapped["Document"] = relationship(back_populates="chunks")


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())

    # Rolling compaction state (ADR 004): `summary` covers every message up
    # to and including `summarized_through_message_id`; messages after that
    # id are still sent verbatim. Both null until compaction first triggers.
    summary: Mapped[str | None] = mapped_column(Text)
    summarized_through_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="SET NULL")
    )

    # Semantic-memory extraction watermark (ADR 008): null until this
    # session is first detected stale (SESSION_INACTIVITY_MINUTES since its
    # last message) and extracted; then holds the id of the last message
    # extraction has processed, so a later check only extracts new turns.
    memory_extracted_through_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="SET NULL")
    )

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", foreign_keys="ChatMessage.session_id"
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (CheckConstraint("role IN ('user','assistant')", name="ck_message_role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    session: Mapped["ChatSession"] = relationship(back_populates="messages", foreign_keys=[session_id])
    citations: Mapped[list["MessageCitation"]] = relationship(back_populates="message", cascade="all, delete-orphan")


class MessageCitation(Base):
    __tablename__ = "message_citations"
    __table_args__ = (UniqueConstraint("message_id", "marker_index", name="uq_citation_message_marker"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False)
    chunk_id: Mapped[int] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False)
    marker_index: Mapped[int] = mapped_column(Integer, nullable=False)

    message: Mapped["ChatMessage"] = relationship(back_populates="citations")
    chunk: Mapped["Chunk"] = relationship()


class QueryTurn(Base):
    """Query-understanding output for one user turn (Phase 1 of the
    retrieval upgrade plan). One row per user-role ChatMessage."""

    __tablename__ = "query_turns"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_query_turn_message"),
        CheckConstraint(
            "intent IN ('factual_lookup','comparison','summarization','follow_up')", name="ck_query_turn_intent"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False)
    intent: Mapped[str] = mapped_column(Text, nullable=False)
    # Null when the query understanding step decided no rewrite was needed
    # (query was already standalone) — the original is never discarded,
    # it's just chat_messages.content for this same message_id.
    rewritten_query: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    message: Mapped["ChatMessage"] = relationship()


class RetrievedChunk(Base):
    """Every chunk retrieve() returned for a turn, not just the ones the
    model ended up citing (see MessageCitation for that subset) — needed to
    score retrieval quality independent of generation quality in Phase 3."""

    __tablename__ = "retrieved_chunks"
    __table_args__ = (UniqueConstraint("message_id", "rank", name="uq_retrieved_chunk_message_rank"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False)
    chunk_id: Mapped[int] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)

    message: Mapped["ChatMessage"] = relationship()
    chunk: Mapped["Chunk"] = relationship()


class Memory(Base):
    """Durable, cross-session fact about the user (Phase 2 of the retrieval
    upgrade plan). Course-scoped, consistent with every other retrieval
    concept in this app being course-scoped. See docs/adr/005-009."""

    __tablename__ = "memories"
    __table_args__ = (
        CheckConstraint(
            "memory_type IN ('topic','struggle','preference','goal')", name="ck_memory_type"
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_memory_confidence_range"),
        Index("memories_course_idx", "course_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    memory_type: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    # Nullable + SET NULL: the source session can be deleted while the
    # memory it produced persists -- that's the entire point of Task 1.
    source_session_id: Mapped[int | None] = mapped_column(ForeignKey("chat_sessions.id", ondelete="SET NULL"))
    access_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_accessed_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
