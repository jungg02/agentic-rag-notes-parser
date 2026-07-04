# Study Notes Parser — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a working, testable Phase 1 of the study notes parser: upload PDFs/DOCX/PPTX into courses, ingest them (convert → parse → chunk → embed), ask questions in a chat scoped to a course, get answers grounded in hybrid-search-retrieved chunks with inline `[n]` citations, and click a citation to open a side panel showing the source PDF at the cited page (no highlight overlay yet — that's Phase 2).

**Architecture:** FastAPI backend with sync SQLAlchemy/psycopg over a local Postgres+pgvector database; ingestion normalizes DOCX/PPTX to PDF via headless LibreOffice and extracts text+bboxes from the PDF with PyMuPDF; retrieval fuses Postgres full-text search and pgvector cosine search via Reciprocal Rank Fusion, reranks with a local cross-encoder, and generation is provider-agnostic (Anthropic/OpenAI) with citation markers resolved server-side. React+Vite frontend with TanStack Query and pdfjs-dist.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 (sync, psycopg3 driver), Alembic, pydantic-settings, PyMuPDF, python-pptx, sentence-transformers, transformers, pytest, httpx; React 18 + Vite + TypeScript, @tanstack/react-query, pdfjs-dist; Docker Compose (pgvector/pgvector:pg17, backend, frontend).

## Global Constraints

- Single-user, no auth anywhere — no users table, no login, no session middleware.
- All retrieval and chat is scoped to exactly one `course_id`; every retrieval query filters by it.
- Database access is **synchronous** SQLAlchemy + `psycopg` (v3) throughout — no asyncpg, no async ORM sessions. FastAPI route functions that touch the DB or run models are plain `def` (not `async def`), so Starlette runs them in its threadpool automatically.
- Embedding model: `BAAI/bge-small-en-v1.5` (384 dimensions) via `sentence-transformers`. Reranker: `cross-encoder/ms-marco-MiniLM-L-6-v2` via `sentence-transformers.CrossEncoder`. Both loaded once as module-level singletons at process startup.
- Chunking never crosses a page boundary. Target ~350 tokens/chunk, ~80 token overlap, tokenized with the embedding model's own tokenizer (`AutoTokenizer.from_pretrained("BAAI/bge-small-en-v1.5")`).
- Fusion of lexical and vector results uses Reciprocal Rank Fusion (`K = 60`), on ranks only — never averaging raw scores.
- DOCX/PPTX are converted to PDF at ingest via headless LibreOffice (`soffice`); all text/bbox extraction happens against the generated PDF, for every format, via PyMuPDF. `python-pptx` is used only to read slide titles as metadata.
- Citation click behavior: clicking a `[n]` chip never navigates away — it opens a slide-out panel showing the PDF at the cited page. Phase 1 has no highlight box (Phase 2 adds it).
- `LLM_PROVIDER=anthropic|openai` is a config value; call sites use only the `LLMProvider` protocol, never a provider SDK directly.
- Every task's tests are run against a **real** Postgres test database (`notes_test`) with the `vector` extension enabled — not mocked, since tsvector/pgvector/HNSW behavior is exactly what's under test. Tests run inside the `backend` Docker container, which has LibreOffice, torch (CPU), and all Python deps installed.

## File Structure

```
docker-compose.yml
.env.example
backend/
  Dockerfile
  pyproject.toml
  alembic.ini
  alembic/
    env.py
    versions/
  app/
    main.py                     # FastAPI app, startup (load embedder/reranker), router includes
    config.py                   # pydantic-settings Settings
    db.py                       # sync engine/session factory, Base
    models.py                   # ORM models
    schemas.py                  # Pydantic request/response schemas
    routers/
      courses.py
      documents.py
      chat.py
      chunks.py
      debug.py
    ingestion/
      convert.py                # LibreOffice -> PDF
      parse.py                  # PyMuPDF lines + bboxes
      chunker.py                # token chunking + context headers
      embedder.py                # sentence-transformers singleton
      pipeline.py                # orchestrates convert -> parse -> chunk -> embed -> persist
    retrieval/
      lexical.py                # tsvector query
      vector.py                 # pgvector query
      fusion.py                 # RRF
      rerank.py                 # CrossEncoder singleton
      service.py                # retrieve(course_id, query) -> top-k
    generation/
      prompts.py                # excerpt formatting, system prompt
      chat_service.py            # retrieval + provider call + citation parsing/persistence
    providers/
      base.py                   # LLMProvider protocol, dataclasses, errors
      anthropic_provider.py
      openai_provider.py
      factory.py
  tests/
    conftest.py                 # test DB fixtures, sample files
    fixtures/
      sample.pdf
      sample.docx
      sample.pptx
    ingestion/
      test_convert.py
      test_parse.py
      test_chunker.py
      test_embedder.py
      test_pipeline.py
    retrieval/
      test_lexical_vector_fusion.py
      test_rerank_service.py
    providers/
      test_providers.py
    generation/
      test_chat_service.py
    routers/
      test_courses.py
      test_documents.py
      test_chat.py
      test_chunks.py
frontend/
  Dockerfile
  package.json
  vite.config.ts
  tsconfig.json
  index.html
  src/
    main.tsx
    App.tsx
    api/
      client.ts
      courses.ts
      documents.ts
      chat.ts
      chunks.ts
    components/
      courses/CourseSelector.tsx
      documents/UploadDropzone.tsx
      documents/DocumentList.tsx
      chat/ChatPane.tsx
      chat/MessageList.tsx
      chat/ChatInput.tsx
      chat/CitationChip.tsx
      source-panel/SourcePanel.tsx
      source-panel/PdfViewer.tsx
```

---

## Task 1: Repo scaffolding, Docker Compose, FastAPI health check

**Files:**
- Create: `docker-compose.yml`
- Create: `.env.example`
- Create: `backend/Dockerfile`
- Create: `backend/pyproject.toml`
- Create: `backend/app/main.py`
- Create: `backend/app/config.py`
- Create: `backend/tests/routers/test_health.py`

**Interfaces:**
- Produces: `app.config.Settings` (pydantic-settings, fields: `database_url: str`, `llm_provider: str`, `llm_model: str`, `llm_api_key: str`, `llm_base_url: str | None`, `data_dir: str = "/data/files"`), `app.config.get_settings() -> Settings`. Produces FastAPI app instance `app.main.app` with `GET /health` returning `{"status": "ok"}`.

- [ ] **Step 1: Write `backend/pyproject.toml`**

```toml
[project]
name = "notes-parser-backend"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "sqlalchemy>=2.0",
    "psycopg[binary]>=3.2",
    "alembic>=1.13",
    "pydantic-settings>=2.4",
    "pgvector>=0.3",
    "pymupdf>=1.24",
    "python-pptx>=1.0",
    "sentence-transformers>=3.0",
    "transformers>=4.44",
    "anthropic>=0.34",
    "openai>=1.40",
    "python-multipart>=0.0.9",
]

[project.optional-dependencies]
dev = ["pytest>=8.3", "pytest-cov>=5.0", "httpx>=0.27"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Write `backend/app/config.py`**

```python
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://notes:notes@db/notes"
    database_url_test: str = "postgresql+psycopg://notes:notes@db/notes_test"

    llm_provider: str = "anthropic"
    llm_model: str = "claude-opus-4-8"
    llm_api_key: str = ""
    llm_base_url: str | None = None

    data_dir: str = "/data/files"

    embedding_model_name: str = "BAAI/bge-small-en-v1.5"
    reranker_model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 3: Write `backend/app/main.py`**

```python
from fastapi import FastAPI

from app.config import get_settings

app = FastAPI(title="Study Notes Parser")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.on_event("startup")
def startup() -> None:
    # Embedder/reranker singletons are loaded lazily on first import
    # (see ingestion/embedder.py, retrieval/rerank.py) rather than here,
    # so importing app.main alone (e.g. in tests) never triggers model
    # downloads.
    get_settings()
```

- [ ] **Step 4: Write `backend/Dockerfile`**

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice --no-install-recommends \
    fonts-liberation \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]" --extra-index-url https://download.pytorch.org/whl/cpu

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
```

- [ ] **Step 5: Write `docker-compose.yml`**

```yaml
services:
  db:
    image: pgvector/pgvector:pg17
    environment:
      POSTGRES_DB: notes
      POSTGRES_USER: notes
      POSTGRES_PASSWORD: notes
    volumes:
      - pgdata:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U notes -d notes"]
      interval: 5s
      timeout: 5s
      retries: 10

  backend:
    build: ./backend
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
    env_file: .env
    environment:
      DATABASE_URL: postgresql+psycopg://notes:notes@db/notes
      DATABASE_URL_TEST: postgresql+psycopg://notes:notes@db/notes_test
    volumes:
      - ./backend:/app
      - filedata:/data/files
      - hf_cache:/root/.cache/huggingface
    depends_on:
      db:
        condition: service_healthy
    ports:
      - "8000:8000"

  frontend:
    build: ./frontend
    command: npm run dev -- --host
    volumes:
      - ./frontend:/app
      - /app/node_modules
    ports:
      - "5173:5173"

volumes:
  pgdata: {}
  filedata: {}
  hf_cache: {}
```

- [ ] **Step 6: Write `.env.example`**

```
LLM_PROVIDER=anthropic
LLM_MODEL=claude-opus-4-8
LLM_API_KEY=sk-changeme
LLM_BASE_URL=
```

- [ ] **Step 7: Write the failing test `backend/tests/routers/test_health.py`**

```python
from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_ok():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 8: Run test to verify it fails (module doesn't exist yet on a clean checkout)**

Run: `docker compose run --rm backend pytest tests/routers/test_health.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app'` or similar, since files were just created; if the earlier steps were followed the app module does exist, so instead expect PASS immediately. If it fails for any reason other than a typo, fix before proceeding.

- [ ] **Step 9: Run test to verify it passes**

Run: `docker compose run --rm backend pytest tests/routers/test_health.py -v`
Expected: `1 passed`

- [ ] **Step 10: Bring the stack up and smoke-test manually**

Run: `docker compose up -d db backend` then `curl http://localhost:8000/health`
Expected: `{"status":"ok"}`

- [ ] **Step 11: Commit**

```bash
git add docker-compose.yml .env.example backend/
git commit -m "feat: scaffold FastAPI backend and docker compose stack"
```

---

## Task 2: Database models and migration

**Files:**
- Create: `backend/app/db.py`
- Create: `backend/app/models.py`
- Create: `backend/alembic.ini`
- Create: `backend/alembic/env.py`
- Create: `backend/alembic/versions/0001_initial_schema.py`
- Create: `backend/tests/conftest.py`
- Test: `backend/tests/test_models.py`

**Interfaces:**
- Consumes: `app.config.get_settings()` (Task 1).
- Produces: `app.db.Base` (declarative base), `app.db.get_engine(url: str)`, `app.db.SessionLocal` (sessionmaker bound to `get_settings().database_url`), `app.db.get_db()` (FastAPI dependency yielding a `Session`), `app.db.get_session_factory()` (FastAPI dependency returning a session-factory callable, defaults to `SessionLocal`, overridable in tests so background tasks can be pointed at a different database/connection than production). Produces ORM models: `Course`, `Document`, `Chunk`, `ChatSession`, `ChatMessage`, `MessageCitation`, all in `app.models`, with columns exactly as listed below — later tasks import these names directly.

- [ ] **Step 1: Write `backend/app/db.py`**

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def get_engine(url: str | None = None):
    return create_engine(url or get_settings().database_url, pool_pre_ping=True)


SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_session_factory():
    """FastAPI dependency provider for a session factory, so background tasks
    that need their own session (rather than the request-scoped `get_db`
    session) can be pointed at a different database in tests via
    `app.dependency_overrides`."""
    return SessionLocal
```

- [ ] **Step 2: Write `backend/app/models.py`**

```python
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Computed,
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

    messages: Mapped[list["ChatMessage"]] = relationship(back_populates="session", cascade="all, delete-orphan")


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (CheckConstraint("role IN ('user','assistant')", name="ck_message_role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    session: Mapped["ChatSession"] = relationship(back_populates="messages")
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
```

- [ ] **Step 3: Write `backend/alembic.ini`**

```ini
[alembic]
script_location = alembic
prepend_sys_path = .
sqlalchemy.url = driver://user:pass@localhost/dbname

[loggers]
keys = root,sqlalchemy,alembic

[logger_root]
level = WARNING
handlers = console

[logger_sqlalchemy]
level = WARNING
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handlers]
keys = console

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatters]
keys = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

- [ ] **Step 4: Write `backend/alembic/env.py`**

```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.db import Base
from app import models  # noqa: F401  (registers models on Base.metadata)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=get_settings().database_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 5: Write `backend/alembic/versions/0001_initial_schema.py`**

```python
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
```

- [ ] **Step 6: Write `backend/tests/conftest.py`**

```python
import os

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_engine
from app.config import get_settings


@pytest.fixture(scope="session")
def test_engine():
    settings = get_settings()
    admin_engine = get_engine(settings.database_url.rsplit("/", 1)[0] + "/notes")
    with admin_engine.connect() as conn:
        conn.execute(text("COMMIT"))
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = 'notes_test'")
        ).first()
        if not exists:
            conn.execute(text("COMMIT"))
            conn.execute(text("CREATE DATABASE notes_test"))
    admin_engine.dispose()

    engine = get_engine(settings.database_url_test)
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(test_engine):
    """Wraps each test in an outer transaction that is always rolled back,
    using the standard SQLAlchemy "join a session into an external
    transaction" pattern (SAVEPOINT + restart-on-end listener). Without
    this, any `session.commit()` call inside application code (nearly
    every route and service commits) would commit straight through the
    connection instead of just releasing a SAVEPOINT, breaking the outer
    rollback and leaking committed rows into later tests.

    Use this fixture for ordinary CRUD tests. Do NOT use it for tests that
    exercise code taking a `db_session_factory`-style callable (e.g.
    `run_ingestion`, or any route that hands a session factory to a
    background task) — that code opens its OWN connection when the factory
    is called, and a second, independently-committing session sharing this
    fixture's connection would commit straight through this fixture's
    transaction too, breaking its rollback. Use `real_db_session` instead
    for those cases (see below).
    """
    connection = test_engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()
    session.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, trans):
        if trans.nested and not trans._parent.nested:
            sess.begin_nested()

    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def real_db_session(test_engine):
    """A plain, actually-committing session bound directly to the shared
    engine (each operation may use a fresh connection from the pool,
    released back to the pool on close — exactly like production).

    Use this ONLY for tests that exercise a `db_session_factory`-style
    callable (e.g. `run_ingestion`, or a route that hands a session factory
    to a `BackgroundTasks` task): that code opens its own separate
    connection/session when the factory is called, so it can only see data
    that was genuinely committed — not data held inside `db_session`'s
    rolled-back transaction on a different connection.

    Tests using this fixture are responsible for cleaning up what they
    create (typically: delete the `Course` you made — it cascades to its
    documents/chunks), since nothing here rolls back automatically.
    """
    Session = sessionmaker(bind=test_engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture()
def fixtures_dir():
    return os.path.join(os.path.dirname(__file__), "fixtures")
```

- [ ] **Step 7: Write the failing test `backend/tests/test_models.py`**

```python
from app.models import Course, Document, Chunk


def test_create_course_document_chunk(db_session):
    course = Course(name="Biology 101")
    db_session.add(course)
    db_session.flush()

    doc = Document(
        course_id=course.id,
        original_filename="week1.pdf",
        original_format="pdf",
        original_path="/data/files/1/original.pdf",
        pdf_path="/data/files/1/original.pdf",
        file_sha256="a" * 64,
    )
    db_session.add(doc)
    db_session.flush()

    chunk = Chunk(
        document_id=doc.id,
        course_id=course.id,
        chunk_index=0,
        text="Mitochondria is the powerhouse of the cell.",
        page_number=1,
        bboxes={"page_width": 612.0, "page_height": 792.0, "rects": [{"x0": 0, "y0": 0, "x1": 1, "y1": 1}]},
        token_count=8,
        embedding=[0.01] * 384,
    )
    db_session.add(chunk)
    db_session.flush()

    assert chunk.id is not None
    assert doc.ingest_status == "pending"
```

- [ ] **Step 8: Run test to verify it fails**

Run: `docker compose run --rm backend pytest tests/test_models.py -v`
Expected: FAIL — database `notes_test` connection error or table-not-found, since the migration hasn't been applied to a running Postgres yet in this environment.

- [ ] **Step 9: Apply the migration and rerun**

Run: `docker compose up -d db` then `docker compose run --rm backend alembic upgrade head` then `docker compose run --rm backend pytest tests/test_models.py -v`
Expected: `1 passed`

- [ ] **Step 10: Commit**

```bash
git add backend/app/db.py backend/app/models.py backend/alembic.ini backend/alembic/ backend/tests/conftest.py backend/tests/test_models.py
git commit -m "feat: add database models and initial migration"
```

---

## Task 3: Ingestion — PDF conversion and parsing

**Files:**
- Create: `backend/app/ingestion/__init__.py`
- Create: `backend/app/ingestion/convert.py`
- Create: `backend/app/ingestion/parse.py`
- Create: `backend/tests/fixtures/sample.pdf` (a small 2-page text PDF — generate with the script in Step 1)
- Create: `backend/tests/fixtures/sample.docx` (a small Word doc with a heading + paragraph — generate with the script in Step 1)
- Test: `backend/tests/ingestion/test_convert.py`
- Test: `backend/tests/ingestion/test_parse.py`

**Interfaces:**
- Produces: `convert.convert_to_pdf(input_path: Path, output_dir: Path) -> Path` (returns path to generated PDF; raises `ConversionError` on failure/timeout). Produces `parse.PageLines` dataclass (`page_number: int`, `width: float`, `height: float`, `rotation: int`, `lines: list[ExtractedLine]`) and `parse.ExtractedLine` dataclass (`text: str`, `bbox: tuple[float, float, float, float]`, `font_size: float`, `bold: bool`). Produces `parse.extract_pages(pdf_path: Path) -> list[PageLines]`.

- [ ] **Step 1: Generate fixture files**

Run this once locally (not part of the test suite) to create the fixtures:

```python
# scripts/make_fixtures.py — run with: python scripts/make_fixtures.py
import fitz
from docx import Document as DocxDocument

doc = fitz.open()
page1 = doc.new_page()
page1.insert_text((72, 100), "Chapter 1: Cell Biology", fontsize=18)
page1.insert_text((72, 140), "The mitochondria is the powerhouse of the cell.", fontsize=11)
page2 = doc.new_page()
page2.insert_text((72, 100), "Chapter 2: Genetics", fontsize=18)
page2.insert_text((72, 140), "DNA carries genetic information in most organisms.", fontsize=11)
doc.save("backend/tests/fixtures/sample.pdf")

docx = DocxDocument()
docx.add_heading("Week 1 Notes", level=1)
docx.add_paragraph("Photosynthesis converts light energy into chemical energy.")
docx.save("backend/tests/fixtures/sample.docx")
```

Requires `python-docx` (already a dependency via `python-pptx`'s ecosystem — add `python-docx>=1.1` to `backend/pyproject.toml` dependencies alongside `python-pptx` since Step 1 here needs it for fixture generation, even though the pipeline itself only converts DOCX via LibreOffice, not python-docx).

- [ ] **Step 2: Write `backend/app/ingestion/__init__.py`** (empty file)

- [ ] **Step 3: Write `backend/app/ingestion/convert.py`**

```python
import subprocess
import threading
import uuid
from pathlib import Path

_LIBREOFFICE_LOCK = threading.Semaphore(1)


class ConversionError(Exception):
    pass


def convert_to_pdf(input_path: Path, output_dir: Path) -> Path:
    """Convert a DOCX/PPTX file to PDF via headless LibreOffice.

    Serialized with a process-wide semaphore because LibreOffice's shared
    user profile lock makes concurrent `soffice` invocations silently fail.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = f"/tmp/lo_profile_{uuid.uuid4().hex}"

    with _LIBREOFFICE_LOCK:
        try:
            subprocess.run(
                [
                    "soffice",
                    "--headless",
                    "--norestore",
                    f"-env:UserInstallation=file://{profile_dir}",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(output_dir),
                    str(input_path),
                ],
                timeout=120,
                check=True,
                capture_output=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise ConversionError(f"LibreOffice conversion failed for {input_path}: {exc}") from exc

    result_path = output_dir / (input_path.stem + ".pdf")
    if not result_path.exists():
        raise ConversionError(f"Expected output {result_path} not found after conversion")
    return result_path
```

- [ ] **Step 4: Write `backend/app/ingestion/parse.py`**

```python
from dataclasses import dataclass
from pathlib import Path

import fitz


@dataclass
class ExtractedLine:
    text: str
    bbox: tuple[float, float, float, float]
    font_size: float
    bold: bool


@dataclass
class PageLines:
    page_number: int
    width: float
    height: float
    rotation: int
    lines: list[ExtractedLine]


def extract_pages(pdf_path: Path) -> list[PageLines]:
    doc = fitz.open(pdf_path)
    pages: list[PageLines] = []
    try:
        for page_index, page in enumerate(doc):
            page_dict = page.get_text("dict")
            lines: list[ExtractedLine] = []
            for block in page_dict.get("blocks", []):
                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    if not spans:
                        continue
                    text = "".join(span["text"] for span in spans).strip()
                    if not text:
                        continue
                    x0 = min(span["bbox"][0] for span in spans)
                    y0 = min(span["bbox"][1] for span in spans)
                    x1 = max(span["bbox"][2] for span in spans)
                    y1 = max(span["bbox"][3] for span in spans)
                    font_size = max(span["size"] for span in spans)
                    bold = any(span["flags"] & 2**4 for span in spans)
                    lines.append(ExtractedLine(text=text, bbox=(x0, y0, x1, y1), font_size=font_size, bold=bold))
            pages.append(
                PageLines(
                    page_number=page_index + 1,
                    width=page.rect.width,
                    height=page.rect.height,
                    rotation=page.rotation,
                    lines=lines,
                )
            )
    finally:
        doc.close()
    return pages
```

- [ ] **Step 5: Write the failing test `backend/tests/ingestion/test_convert.py`**

```python
from pathlib import Path

from app.ingestion.convert import convert_to_pdf


def test_convert_docx_to_pdf(fixtures_dir, tmp_path):
    input_path = Path(fixtures_dir) / "sample.docx"
    output = convert_to_pdf(input_path, tmp_path)
    assert output.exists()
    assert output.suffix == ".pdf"
    assert output.stat().st_size > 0
```

- [ ] **Step 6: Run test to verify it fails**

Run: `docker compose run --rm backend pytest tests/ingestion/test_convert.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ingestion'` before Step 2/3 are in place; once they exist, this should already pass, so if it still fails after writing the files, check `soffice` is on PATH inside the container (`docker compose run --rm backend which soffice`).

- [ ] **Step 7: Run test to verify it passes**

Run: `docker compose run --rm backend pytest tests/ingestion/test_convert.py -v`
Expected: `1 passed`

- [ ] **Step 8: Write the failing test `backend/tests/ingestion/test_parse.py`**

```python
from pathlib import Path

from app.ingestion.parse import extract_pages


def test_extract_pages_returns_text_and_bboxes(fixtures_dir):
    pages = extract_pages(Path(fixtures_dir) / "sample.pdf")

    assert len(pages) == 2
    assert pages[0].page_number == 1
    assert pages[0].width > 0 and pages[0].height > 0

    all_text = " ".join(line.text for line in pages[0].lines)
    assert "Cell Biology" in all_text
    assert "mitochondria" in all_text.lower()

    heading_line = next(line for line in pages[0].lines if "Cell Biology" in line.text)
    assert heading_line.font_size > 14
    for x in heading_line.bbox:
        assert isinstance(x, float)


def test_extract_pages_second_page(fixtures_dir):
    pages = extract_pages(Path(fixtures_dir) / "sample.pdf")
    all_text = " ".join(line.text for line in pages[1].lines)
    assert "Genetics" in all_text
```

- [ ] **Step 9: Run test to verify it fails, then passes**

Run: `docker compose run --rm backend pytest tests/ingestion/test_parse.py -v`
Expected: first run before `parse.py` exists FAILs with `ModuleNotFoundError`; after Step 4, `2 passed`.

- [ ] **Step 10: Commit**

```bash
git add backend/app/ingestion/ backend/tests/ingestion/test_convert.py backend/tests/ingestion/test_parse.py backend/tests/fixtures/ backend/pyproject.toml scripts/make_fixtures.py
git commit -m "feat: add LibreOffice conversion and PyMuPDF parsing for ingestion"
```

---

## Task 4: Ingestion — chunking with context headers

**Files:**
- Create: `backend/app/ingestion/chunker.py`
- Test: `backend/tests/ingestion/test_chunker.py`

**Interfaces:**
- Consumes: `parse.PageLines`, `parse.ExtractedLine` (Task 3).
- Produces: `chunker.ChunkDraft` dataclass (`text: str`, `context_header: str | None`, `page_number: int`, `bboxes: dict`, `token_count: int`) and `chunker.chunk_pages(pages: list[PageLines], target_tokens: int = 350, overlap_tokens: int = 80) -> list[ChunkDraft]`. `bboxes` shape: `{"page_width": float, "page_height": float, "rects": [{"x0": float, "y0": float, "x1": float, "y1": float}, ...]}`.

- [ ] **Step 1: Write `backend/app/ingestion/chunker.py`**

```python
from dataclasses import dataclass, field

from transformers import AutoTokenizer

from app.ingestion.parse import ExtractedLine, PageLines

_TOKENIZER = None


def _tokenizer():
    global _TOKENIZER
    if _TOKENIZER is None:
        _TOKENIZER = AutoTokenizer.from_pretrained("BAAI/bge-small-en-v1.5")
    return _TOKENIZER


def _token_count(text: str) -> int:
    return len(_tokenizer().encode(text, add_special_tokens=False))


@dataclass
class ChunkDraft:
    text: str
    context_header: str | None
    page_number: int
    bboxes: dict
    token_count: int


def _detect_header(lines: list[ExtractedLine]) -> str | None:
    """Largest-font, bold, top-of-page line is treated as this page's heading."""
    if not lines:
        return None
    candidates = [line for line in lines if line.bold or line.font_size >= 14]
    if not candidates:
        return None
    return max(candidates, key=lambda line: (line.font_size, -line.bbox[1])).text


def _merge_rects(lines: list[ExtractedLine]) -> list[dict]:
    return [{"x0": l.bbox[0], "y0": l.bbox[1], "x1": l.bbox[2], "y1": l.bbox[3]} for l in lines]


def _make_chunk(lines: list[ExtractedLine], page: PageLines, header: str | None) -> ChunkDraft:
    text = "\n".join(line.text for line in lines)
    embed_text = f"{header}\n{text}" if header else text
    return ChunkDraft(
        text=text,
        context_header=header,
        page_number=page.page_number,
        bboxes={"page_width": page.width, "page_height": page.height, "rects": _merge_rects(lines)},
        token_count=_token_count(embed_text),
    )


def chunk_pages(pages: list[PageLines], target_tokens: int = 350, overlap_tokens: int = 80) -> list[ChunkDraft]:
    chunks: list[ChunkDraft] = []

    for page in pages:
        if not page.lines:
            continue

        header = _detect_header(page.lines)
        body_lines = [line for line in page.lines if line.text != header]

        current: list[ExtractedLine] = []
        current_tokens = 0

        for line in body_lines:
            line_tokens = _token_count(line.text)
            if current and current_tokens + line_tokens > target_tokens:
                chunks.append(_make_chunk(current, page, header))
                # carry the trailing lines forward as overlap
                overlap: list[ExtractedLine] = []
                overlap_tok = 0
                for prev_line in reversed(current):
                    t = _token_count(prev_line.text)
                    if overlap_tok + t > overlap_tokens:
                        break
                    overlap.insert(0, prev_line)
                    overlap_tok += t
                current = overlap
                current_tokens = overlap_tok

            current.append(line)
            current_tokens += line_tokens

        if current:
            chunks.append(_make_chunk(current, page, header))
        elif header and not body_lines:
            # header-only page (e.g. a title slide) still gets one small chunk
            chunks.append(_make_chunk([], page, header) if False else _make_chunk(
                [ExtractedLine(text=header, bbox=(0, 0, page.width, 20), font_size=18, bold=True)], page, None
            ))

    return chunks
```

- [ ] **Step 2: Write the failing test `backend/tests/ingestion/test_chunker.py`**

```python
from app.ingestion.chunker import chunk_pages
from app.ingestion.parse import ExtractedLine, PageLines


def _line(text, y=100, size=11, bold=False):
    return ExtractedLine(text=text, bbox=(72.0, y, 500.0, y + 14), font_size=size, bold=bold)


def test_single_short_page_produces_one_chunk_with_header():
    page = PageLines(
        page_number=1,
        width=612.0,
        height=792.0,
        rotation=0,
        lines=[
            _line("Lecture 4: Photosynthesis", y=100, size=18, bold=True),
            _line("Light reactions occur in the thylakoid membrane.", y=140),
            _line("Dark reactions occur in the stroma.", y=160),
        ],
    )

    chunks = chunk_pages([page])

    assert len(chunks) == 1
    assert chunks[0].context_header == "Lecture 4: Photosynthesis"
    assert "thylakoid" in chunks[0].text
    assert chunks[0].page_number == 1
    assert chunks[0].bboxes["page_width"] == 612.0
    assert len(chunks[0].bboxes["rects"]) == 2  # header line excluded from body rects


def test_long_page_splits_into_multiple_chunks_with_overlap():
    body_lines = [_line(f"Sentence number {i} about cell biology topics in detail.", y=100 + i * 14) for i in range(80)]
    page = PageLines(page_number=2, width=612.0, height=792.0, rotation=0, lines=[_line("Overview", size=18, bold=True)] + body_lines)

    chunks = chunk_pages([page], target_tokens=100, overlap_tokens=20)

    assert len(chunks) > 1
    assert all(c.page_number == 2 for c in chunks)
    assert all(c.context_header == "Overview" for c in chunks)


def test_chunks_never_span_pages():
    page1 = PageLines(page_number=1, width=612, height=792, rotation=0, lines=[_line("Page one content sentence.")])
    page2 = PageLines(page_number=2, width=612, height=792, rotation=0, lines=[_line("Page two content sentence.")])

    chunks = chunk_pages([page1, page2])

    pages_seen = {c.page_number for c in chunks}
    assert pages_seen == {1, 2}
    for c in chunks:
        assert c.page_number in (1, 2)
```

- [ ] **Step 3: Run test to verify it fails, then passes**

Run: `docker compose run --rm backend pytest tests/ingestion/test_chunker.py -v`
Expected: FAIL with `ModuleNotFoundError` before Step 1; `3 passed` after.

- [ ] **Step 4: Commit**

```bash
git add backend/app/ingestion/chunker.py backend/tests/ingestion/test_chunker.py
git commit -m "feat: add page-bounded chunking with carried-forward context headers"
```

---

## Task 5: Ingestion — embedding model wrapper

**Files:**
- Create: `backend/app/ingestion/embedder.py`
- Test: `backend/tests/ingestion/test_embedder.py`

**Interfaces:**
- Produces: `embedder.embed_texts(texts: list[str]) -> list[list[float]]` (batch passage embedding, 384-dim, normalized), `embedder.embed_query(query: str) -> list[float]` (applies the bge query-prefix convention).

- [ ] **Step 1: Write `backend/app/ingestion/embedder.py`**

```python
from sentence_transformers import SentenceTransformer

_MODEL = None

_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def _model() -> SentenceTransformer:
    global _MODEL
    if _MODEL is None:
        _MODEL = SentenceTransformer("BAAI/bge-small-en-v1.5", device="cpu")
    return _MODEL


def embed_texts(texts: list[str]) -> list[list[float]]:
    vectors = _model().encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vectors]


def embed_query(query: str) -> list[float]:
    vector = _model().encode(_QUERY_PREFIX + query, normalize_embeddings=True, show_progress_bar=False)
    return vector.tolist()
```

- [ ] **Step 2: Write the failing test `backend/tests/ingestion/test_embedder.py`**

```python
import math

from app.ingestion.embedder import embed_query, embed_texts


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def test_embed_texts_returns_384_dim_normalized_vectors():
    vectors = embed_texts(["The mitochondria is the powerhouse of the cell."])
    assert len(vectors) == 1
    assert len(vectors[0]) == 384
    norm = math.sqrt(sum(x * x for x in vectors[0]))
    assert abs(norm - 1.0) < 1e-3


def test_similar_sentences_score_higher_than_dissimilar():
    vectors = embed_texts([
        "The mitochondria is the powerhouse of the cell.",
        "Mitochondria generate ATP through cellular respiration.",
        "The stock market closed lower today amid inflation fears.",
    ])
    sim_related = _cosine(vectors[0], vectors[1])
    sim_unrelated = _cosine(vectors[0], vectors[2])
    assert sim_related > sim_unrelated


def test_embed_query_uses_prefix_and_returns_384_dim():
    vector = embed_query("what does the mitochondria do?")
    assert len(vector) == 384
```

- [ ] **Step 3: Run test to verify it fails, then passes**

Run: `docker compose run --rm backend pytest tests/ingestion/test_embedder.py -v`
Expected: FAIL with `ModuleNotFoundError` before Step 1 (and the first run after will download ~130MB of model weights into the `hf_cache` volume — subsequent runs are fast); `3 passed` after.

- [ ] **Step 4: Commit**

```bash
git add backend/app/ingestion/embedder.py backend/tests/ingestion/test_embedder.py
git commit -m "feat: add local embedding model wrapper"
```

---

## Task 6: Ingestion pipeline orchestration and persistence

**Files:**
- Create: `backend/app/ingestion/pipeline.py`
- Test: `backend/tests/ingestion/test_pipeline.py`

**Interfaces:**
- Consumes: `convert.convert_to_pdf`, `parse.extract_pages`, `chunker.chunk_pages`, `embedder.embed_texts` (Tasks 3-5); `models.Document`, `models.Chunk` (Task 2).
- Produces: `pipeline.MIN_TEXT_CHARS_PER_PAGE = 50` (module constant), `pipeline.run_ingestion(document_id: int, db_session_factory: Callable[[], Session]) -> None` — reads the `Document` row, runs convert→parse→chunk→embed, writes `Chunk` rows, and updates `ingest_status`/`ingest_error`/`page_count` at each stage using a **fresh session per call** obtained from `db_session_factory` (so it works whether invoked in a request thread or a background thread).

- [ ] **Step 1: Write `backend/app/ingestion/pipeline.py`**

```python
from pathlib import Path
from typing import Callable

from sqlalchemy.orm import Session

from app.ingestion.chunker import chunk_pages
from app.ingestion.convert import ConversionError, convert_to_pdf
from app.ingestion.embedder import embed_texts
from app.ingestion.parse import extract_pages
from app.models import Chunk, Document

MIN_TEXT_CHARS_PER_PAGE = 50


class IngestionError(Exception):
    pass


def _set_status(db: Session, document_id: int, status: str, error: str | None = None) -> None:
    doc = db.get(Document, document_id)
    doc.ingest_status = status
    doc.ingest_error = error
    db.commit()


def run_ingestion(document_id: int, db_session_factory: Callable[[], Session]) -> None:
    db = db_session_factory()
    try:
        doc = db.get(Document, document_id)
        if doc is None:
            return

        try:
            if doc.original_format == "pdf":
                pdf_path = Path(doc.original_path)
            else:
                _set_status(db, document_id, "converting")
                output_dir = Path(doc.original_path).parent
                pdf_path = convert_to_pdf(Path(doc.original_path), output_dir)

            doc = db.get(Document, document_id)
            doc.pdf_path = str(pdf_path)
            db.commit()

            _set_status(db, document_id, "parsing")
            pages = extract_pages(pdf_path)

            total_chars = sum(len(line.text) for page in pages for line in page.lines)
            if pages and total_chars < MIN_TEXT_CHARS_PER_PAGE * len(pages):
                raise IngestionError("No extractable text found (scanned document?)")

            drafts = chunk_pages(pages)
            if not drafts:
                raise IngestionError("No chunks produced from document")

            _set_status(db, document_id, "embedding")
            embed_inputs = [f"{d.context_header}\n{d.text}" if d.context_header else d.text for d in drafts]
            vectors = embed_texts(embed_inputs)

            for index, (draft, vector) in enumerate(zip(drafts, vectors)):
                db.add(
                    Chunk(
                        document_id=document_id,
                        course_id=doc.course_id,
                        chunk_index=index,
                        text=draft.text,
                        context_header=draft.context_header,
                        page_number=draft.page_number,
                        bboxes=draft.bboxes,
                        token_count=draft.token_count,
                        embedding=vector,
                    )
                )

            doc = db.get(Document, document_id)
            doc.page_count = len(pages)
            doc.ingest_status = "ready"
            doc.ingest_error = None
            db.commit()

        except (ConversionError, IngestionError) as exc:
            db.rollback()
            _set_status(db, document_id, "failed", str(exc))
        except Exception as exc:  # noqa: BLE001 - any unexpected failure must not crash the background task
            db.rollback()
            _set_status(db, document_id, "failed", f"Unexpected error: {exc}")
    finally:
        db.close()
```

- [ ] **Step 2: Write the failing test `backend/tests/ingestion/test_pipeline.py`**

```python
import shutil
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from app.ingestion.pipeline import run_ingestion
from app.models import Chunk, Course, Document


def test_run_ingestion_pdf_end_to_end(real_db_session, test_engine, fixtures_dir, tmp_path):
    course = Course(name="Pipeline Test Course PDF")
    real_db_session.add(course)
    real_db_session.commit()

    try:
        doc_dir = tmp_path / "doc1"
        doc_dir.mkdir()
        original = doc_dir / "original.pdf"
        shutil.copy(Path(fixtures_dir) / "sample.pdf", original)

        document = Document(
            course_id=course.id,
            original_filename="sample.pdf",
            original_format="pdf",
            original_path=str(original),
            file_sha256="b" * 64,
        )
        real_db_session.add(document)
        real_db_session.commit()
        document_id = document.id

        # A fresh connection from the pool each call — exactly like
        # production. This only works because `real_db_session`'s writes
        # above were genuinely committed, so this separate connection can
        # see them.
        session_factory = sessionmaker(bind=test_engine)
        run_ingestion(document_id, session_factory)

        real_db_session.expire_all()
        refreshed = real_db_session.get(Document, document_id)
        assert refreshed.ingest_status == "ready"
        assert refreshed.page_count == 2

        chunks = real_db_session.query(Chunk).filter_by(document_id=document_id).order_by(Chunk.chunk_index).all()
        assert len(chunks) >= 2
        assert all(c.course_id == course.id for c in chunks)
        assert any("mitochondria" in c.text.lower() for c in chunks)
    finally:
        real_db_session.delete(course)
        real_db_session.commit()


def test_run_ingestion_docx_converts_and_embeds(real_db_session, test_engine, fixtures_dir, tmp_path):
    course = Course(name="Pipeline Test Course DOCX")
    real_db_session.add(course)
    real_db_session.commit()

    try:
        doc_dir = tmp_path / "doc2"
        doc_dir.mkdir()
        original = doc_dir / "original.docx"
        shutil.copy(Path(fixtures_dir) / "sample.docx", original)

        document = Document(
            course_id=course.id,
            original_filename="sample.docx",
            original_format="docx",
            original_path=str(original),
            file_sha256="c" * 64,
        )
        real_db_session.add(document)
        real_db_session.commit()
        document_id = document.id

        session_factory = sessionmaker(bind=test_engine)
        run_ingestion(document_id, session_factory)

        real_db_session.expire_all()
        refreshed = real_db_session.get(Document, document_id)
        assert refreshed.ingest_status == "ready"
        assert refreshed.pdf_path is not None
        assert refreshed.pdf_path.endswith(".pdf")
    finally:
        real_db_session.delete(course)
        real_db_session.commit()
```

- [ ] **Step 3: Run test to verify it fails, then passes**

Run: `docker compose run --rm backend pytest tests/ingestion/test_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError` before Step 1; `2 passed` after. Both tests use `real_db_session` (genuinely committing) for setup so that `run_ingestion`'s independently-connected session (built from `session_factory = sessionmaker(bind=test_engine)`, a fresh connection from the pool) can actually see the seeded `Course`/`Document` rows — this mirrors how the real app uses `run_ingestion` in production. Each test deletes its `Course` in a `finally` block (cascades to `Document`/`Chunk`) since nothing here rolls back automatically.

- [ ] **Step 4: Commit**

```bash
git add backend/app/ingestion/pipeline.py backend/tests/ingestion/test_pipeline.py
git commit -m "feat: orchestrate ingestion pipeline with status tracking"
```

---

## Task 7: Courses API

**Files:**
- Create: `backend/app/schemas.py`
- Create: `backend/app/routers/__init__.py`
- Create: `backend/app/routers/courses.py`
- Modify: `backend/app/main.py` (register router)
- Test: `backend/tests/routers/test_courses.py`

**Interfaces:**
- Consumes: `models.Course` (Task 2), `db.get_db` (Task 2).
- Produces: `schemas.CourseCreate`, `schemas.CourseUpdate`, `schemas.CourseOut` (Pydantic models, `CourseOut` has `id, name, created_at, document_count`). Produces router mounted at `/api/courses` with `POST /`, `GET /`, `PATCH /{id}`, `DELETE /{id}`.

- [ ] **Step 1: Write `backend/app/schemas.py`**

```python
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CourseCreate(BaseModel):
    name: str


class CourseUpdate(BaseModel):
    name: str


class CourseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    created_at: datetime
    document_count: int = 0
```

- [ ] **Step 2: Write `backend/app/routers/__init__.py`** (empty file)

- [ ] **Step 3: Write `backend/app/routers/courses.py`**

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Course, Document
from app.schemas import CourseCreate, CourseOut, CourseUpdate

router = APIRouter(prefix="/api/courses", tags=["courses"])


@router.post("", response_model=CourseOut, status_code=201)
def create_course(payload: CourseCreate, db: Session = Depends(get_db)):
    existing = db.scalar(select(Course).where(Course.name == payload.name))
    if existing:
        raise HTTPException(status_code=409, detail="A course with this name already exists")
    course = Course(name=payload.name)
    db.add(course)
    db.commit()
    db.refresh(course)
    return CourseOut(id=course.id, name=course.name, created_at=course.created_at, document_count=0)


@router.get("", response_model=list[CourseOut])
def list_courses(db: Session = Depends(get_db)):
    rows = db.execute(
        select(Course, func.count(Document.id))
        .outerjoin(Document, Document.course_id == Course.id)
        .group_by(Course.id)
        .order_by(Course.created_at)
    ).all()
    return [
        CourseOut(id=c.id, name=c.name, created_at=c.created_at, document_count=count)
        for c, count in rows
    ]


@router.patch("/{course_id}", response_model=CourseOut)
def update_course(course_id: int, payload: CourseUpdate, db: Session = Depends(get_db)):
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    course.name = payload.name
    db.commit()
    db.refresh(course)
    doc_count = db.scalar(select(func.count(Document.id)).where(Document.course_id == course_id))
    return CourseOut(id=course.id, name=course.name, created_at=course.created_at, document_count=doc_count)


@router.delete("/{course_id}", status_code=204)
def delete_course(course_id: int, db: Session = Depends(get_db)):
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    db.delete(course)
    db.commit()
```

- [ ] **Step 4: Modify `backend/app/main.py`** to register the router

```python
from fastapi import FastAPI

from app.config import get_settings
from app.routers import courses

app = FastAPI(title="Study Notes Parser")
app.include_router(courses.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.on_event("startup")
def startup() -> None:
    get_settings()
```

- [ ] **Step 5: Write the failing test `backend/tests/routers/test_courses.py`**

```python
import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_create_and_list_courses(client):
    response = client.post("/api/courses", json={"name": "Organic Chemistry"})
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Organic Chemistry"
    assert body["document_count"] == 0

    response = client.get("/api/courses")
    assert response.status_code == 200
    names = [c["name"] for c in response.json()]
    assert "Organic Chemistry" in names


def test_create_duplicate_course_name_returns_409(client):
    client.post("/api/courses", json={"name": "Physics"})
    response = client.post("/api/courses", json={"name": "Physics"})
    assert response.status_code == 409


def test_update_and_delete_course(client):
    created = client.post("/api/courses", json={"name": "History"}).json()
    course_id = created["id"]

    response = client.patch(f"/api/courses/{course_id}", json={"name": "World History"})
    assert response.status_code == 200
    assert response.json()["name"] == "World History"

    response = client.delete(f"/api/courses/{course_id}")
    assert response.status_code == 204

    response = client.get("/api/courses")
    assert course_id not in [c["id"] for c in response.json()]
```

- [ ] **Step 6: Run test to verify it fails, then passes**

Run: `docker compose run --rm backend pytest tests/routers/test_courses.py -v`
Expected: FAIL with `ModuleNotFoundError: app.schemas` before Steps 1-4; `3 passed` after.

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas.py backend/app/routers/ backend/app/main.py backend/tests/routers/test_courses.py
git commit -m "feat: add course CRUD API"
```

---

## Task 8: Documents API — upload, list, status, retry, delete, PDF streaming

**Files:**
- Create: `backend/app/routers/documents.py`
- Modify: `backend/app/schemas.py` (add `DocumentOut`)
- Modify: `backend/app/config.py` (already has `data_dir`, no change needed — referenced here for clarity)
- Modify: `backend/app/main.py` (register router)
- Test: `backend/tests/routers/test_documents.py`

**Interfaces:**
- Consumes: `models.Document`, `ingestion.pipeline.run_ingestion`, `db.get_session_factory` (Task 2 — injected via `Depends` and passed as the `db_session_factory` argument to background ingestion, so tests can override it to target the test database instead of production).
- Produces: `schemas.DocumentOut` (`id, course_id, original_filename, original_format, ingest_status, ingest_error, page_count, created_at`). Router mounted with `POST /api/courses/{course_id}/documents`, `GET /api/courses/{course_id}/documents`, `GET /api/documents/{id}`, `POST /api/documents/{id}/retry`, `DELETE /api/documents/{id}`, `GET /api/documents/{id}/pdf`.

- [ ] **Step 1: Add to `backend/app/schemas.py`**

```python
class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    course_id: int
    original_filename: str
    original_format: str
    ingest_status: str
    ingest_error: str | None
    page_count: int | None
    created_at: datetime
```

- [ ] **Step 2: Write `backend/app/routers/documents.py`**

```python
import hashlib
import shutil
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db, get_session_factory
from app.ingestion.pipeline import run_ingestion
from app.models import Course, Document
from app.schemas import DocumentOut

router = APIRouter(tags=["documents"])

_ALLOWED_EXTENSIONS = {".pdf": "pdf", ".docx": "docx", ".pptx": "pptx"}


def _store_upload(course_id: int, upload: UploadFile) -> tuple[Path, str, str]:
    ext = Path(upload.filename).suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}")

    data = upload.file.read()
    sha256 = hashlib.sha256(data).hexdigest()

    doc_dir = Path(get_settings().data_dir) / f"course_{course_id}" / sha256
    doc_dir.mkdir(parents=True, exist_ok=True)
    dest = doc_dir / f"original{ext}"
    dest.write_bytes(data)

    return dest, sha256, _ALLOWED_EXTENSIONS[ext]


@router.post("/api/courses/{course_id}/documents", response_model=list[DocumentOut], status_code=202)
def upload_documents(
    course_id: int,
    files: list[UploadFile],
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    session_factory=Depends(get_session_factory),
):
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")

    created: list[Document] = []
    for upload in files:
        dest, sha256, fmt = _store_upload(course_id, upload)

        existing = db.scalar(
            select(Document).where(Document.course_id == course_id, Document.file_sha256 == sha256)
        )
        if existing:
            created.append(existing)
            continue

        document = Document(
            course_id=course_id,
            original_filename=upload.filename,
            original_format=fmt,
            original_path=str(dest),
            file_sha256=sha256,
        )
        db.add(document)
        db.flush()
        created.append(document)

    db.commit()
    for document in created:
        db.refresh(document)
        background_tasks.add_task(run_ingestion, document.id, session_factory)

    return created


@router.get("/api/courses/{course_id}/documents", response_model=list[DocumentOut])
def list_documents(course_id: int, db: Session = Depends(get_db)):
    return db.scalars(select(Document).where(Document.course_id == course_id).order_by(Document.created_at)).all()


@router.get("/api/documents/{document_id}", response_model=DocumentOut)
def get_document(document_id: int, db: Session = Depends(get_db)):
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


@router.post("/api/documents/{document_id}/retry", response_model=DocumentOut)
def retry_document(
    document_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    session_factory=Depends(get_session_factory),
):
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    document.ingest_status = "pending"
    document.ingest_error = None
    db.commit()
    db.refresh(document)
    background_tasks.add_task(run_ingestion, document.id, session_factory)
    return document


@router.delete("/api/documents/{document_id}", status_code=204)
def delete_document(document_id: int, db: Session = Depends(get_db)):
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    doc_dir = Path(document.original_path).parent
    db.delete(document)
    db.commit()
    shutil.rmtree(doc_dir, ignore_errors=True)


@router.get("/api/documents/{document_id}/pdf")
def get_document_pdf(document_id: int, db: Session = Depends(get_db)):
    document = db.get(Document, document_id)
    if document is None or not document.pdf_path:
        raise HTTPException(status_code=404, detail="PDF not available")
    return FileResponse(document.pdf_path, media_type="application/pdf")
```

- [ ] **Step 3: Modify `backend/app/main.py`**

```python
from fastapi import FastAPI

from app.config import get_settings
from app.routers import courses, documents

app = FastAPI(title="Study Notes Parser")
app.include_router(courses.router)
app.include_router(documents.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.on_event("startup")
def startup() -> None:
    get_settings()
```

- [ ] **Step 4: Write the failing test `backend/tests/routers/test_documents.py`**

```python
import io
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.db import get_db, get_session_factory
from app.main import app
from app.models import Course


@pytest.fixture()
def client(real_db_session, test_engine, tmp_path, monkeypatch):
    """Uses `real_db_session` (not the rolled-back `db_session`) and
    overrides `get_session_factory` to bind background ingestion to the
    same test engine — both must be "real, committing" together, since the
    background task opens its own connection and can only see genuinely
    committed rows. See `real_db_session`'s docstring in conftest.py."""
    from app import config

    monkeypatch.setattr(config.get_settings(), "data_dir", str(tmp_path))
    app.dependency_overrides[get_db] = lambda: real_db_session
    app.dependency_overrides[get_session_factory] = lambda: sessionmaker(bind=test_engine)
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def course(real_db_session):
    course = Course(name="Upload Test Course")
    real_db_session.add(course)
    real_db_session.commit()
    yield course
    real_db_session.delete(course)
    real_db_session.commit()


def test_upload_pdf_starts_ingestion_and_becomes_ready(client, course, fixtures_dir):
    pdf_bytes = Path(fixtures_dir, "sample.pdf").read_bytes()

    response = client.post(
        f"/api/courses/{course.id}/documents",
        files={"files": ("sample.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )
    assert response.status_code == 202
    document_id = response.json()[0]["id"]

    # BackgroundTasks in TestClient run synchronously before the response
    # returns in this FastAPI version's test transport, but poll defensively
    # in case that ever changes.
    deadline = time.time() + 30
    status = None
    while time.time() < deadline:
        status = client.get(f"/api/documents/{document_id}").json()["ingest_status"]
        if status in ("ready", "failed"):
            break
        time.sleep(0.5)

    assert status == "ready"


def test_upload_rejects_unsupported_extension(client, course):
    response = client.post(
        f"/api/courses/{course.id}/documents",
        files={"files": ("notes.txt", io.BytesIO(b"hello"), "text/plain")},
    )
    assert response.status_code == 400


def test_delete_document_removes_it(client, course, fixtures_dir):
    pdf_bytes = Path(fixtures_dir, "sample.pdf").read_bytes()
    upload = client.post(
        f"/api/courses/{course.id}/documents",
        files={"files": ("sample.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )
    document_id = upload.json()[0]["id"]

    response = client.delete(f"/api/documents/{document_id}")
    assert response.status_code == 204

    response = client.get(f"/api/documents/{document_id}")
    assert response.status_code == 404
```

- [ ] **Step 5: Run test to verify it fails, then passes**

Run: `docker compose run --rm backend pytest tests/routers/test_documents.py -v`
Expected: FAIL before Steps 1-3 with `ModuleNotFoundError`; `3 passed` after (the first upload test is slow — real ingestion including model inference runs inline). The `course` fixture deletes its row (cascading to any documents/chunks) after each test since `real_db_session` does not roll back automatically.

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas.py backend/app/routers/documents.py backend/app/main.py backend/tests/routers/test_documents.py
git commit -m "feat: add document upload, status, retry, delete, and PDF streaming API"
```

---

## Task 9: Hybrid retrieval — lexical, vector, RRF fusion

**Files:**
- Create: `backend/app/retrieval/__init__.py`
- Create: `backend/app/retrieval/lexical.py`
- Create: `backend/app/retrieval/vector.py`
- Create: `backend/app/retrieval/fusion.py`
- Test: `backend/tests/retrieval/test_lexical_vector_fusion.py`

**Interfaces:**
- Consumes: `models.Chunk` (Task 2), `embedder.embed_query` (Task 5).
- Produces: `lexical.search_lexical(db: Session, course_id: int, query: str, limit: int = 50) -> list[int]` (ordered chunk ids), `vector.search_vector(db: Session, course_id: int, query_embedding: list[float], limit: int = 50) -> list[int]`, `fusion.reciprocal_rank_fusion(ranked_lists: list[list[int]], k: int = 60) -> list[int]`.

- [ ] **Step 1: Write `backend/app/retrieval/__init__.py`** (empty file)

- [ ] **Step 2: Write `backend/app/retrieval/lexical.py`**

```python
from sqlalchemy import text
from sqlalchemy.orm import Session


def search_lexical(db: Session, course_id: int, query: str, limit: int = 50) -> list[int]:
    rows = db.execute(
        text(
            """
            SELECT id
            FROM chunks, websearch_to_tsquery('english', :query) q
            WHERE course_id = :course_id AND tsv @@ q
            ORDER BY ts_rank_cd(tsv, q) DESC
            LIMIT :limit
            """
        ),
        {"query": query, "course_id": course_id, "limit": limit},
    ).all()
    return [row[0] for row in rows]
```

- [ ] **Step 3: Write `backend/app/retrieval/vector.py`**

```python
from sqlalchemy import text
from sqlalchemy.orm import Session


def search_vector(db: Session, course_id: int, query_embedding: list[float], limit: int = 50) -> list[int]:
    rows = db.execute(
        text(
            """
            SELECT id
            FROM chunks
            WHERE course_id = :course_id
            ORDER BY embedding <=> (:query_embedding)::vector
            LIMIT :limit
            """
        ),
        {"course_id": course_id, "query_embedding": str(query_embedding), "limit": limit},
    ).all()
    return [row[0] for row in rows]
```

- [ ] **Step 4: Write `backend/app/retrieval/fusion.py`**

```python
from collections import defaultdict


def reciprocal_rank_fusion(ranked_lists: list[list[int]], k: int = 60) -> list[int]:
    scores: dict[int, float] = defaultdict(float)
    for ranked in ranked_lists:
        for rank, chunk_id in enumerate(ranked):
            scores[chunk_id] += 1.0 / (k + rank + 1)
    return [chunk_id for chunk_id, _ in sorted(scores.items(), key=lambda kv: -kv[1])]
```

- [ ] **Step 5: Write the failing test `backend/tests/retrieval/test_lexical_vector_fusion.py`**

```python
from app.ingestion.embedder import embed_query, embed_texts
from app.models import Chunk, Course, Document
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.lexical import search_lexical
from app.retrieval.vector import search_vector


def _seed_course_with_chunks(db_session):
    course = Course(name="Retrieval Test Course")
    db_session.add(course)
    db_session.flush()

    document = Document(
        course_id=course.id,
        original_filename="doc.pdf",
        original_format="pdf",
        original_path="/tmp/doc.pdf",
        file_sha256="d" * 64,
    )
    db_session.add(document)
    db_session.flush()

    texts = [
        "Mitochondria is the powerhouse of the cell and produces ATP.",
        "Photosynthesis converts sunlight into chemical energy in plants.",
        "The French Revolution began in 1789 and reshaped European politics.",
    ]
    vectors = embed_texts(texts)

    chunks = []
    for i, (t, v) in enumerate(zip(texts, vectors)):
        chunk = Chunk(
            document_id=document.id,
            course_id=course.id,
            chunk_index=i,
            text=t,
            page_number=1,
            bboxes={"page_width": 612.0, "page_height": 792.0, "rects": []},
            token_count=10,
            embedding=v,
        )
        db_session.add(chunk)
        chunks.append(chunk)
    db_session.commit()
    return course, chunks


def test_lexical_search_finds_keyword_match(db_session):
    course, chunks = _seed_course_with_chunks(db_session)
    results = search_lexical(db_session, course.id, "mitochondria ATP")
    assert results
    assert results[0] == chunks[0].id


def test_lexical_search_scoped_to_course(db_session):
    course, chunks = _seed_course_with_chunks(db_session)
    other_course, _ = _seed_course_with_chunks(db_session)
    results = search_lexical(db_session, other_course.id, "mitochondria")
    assert chunks[0].id not in results


def test_vector_search_finds_semantic_match(db_session):
    course, chunks = _seed_course_with_chunks(db_session)
    query_embedding = embed_query("what generates energy in a cell?")
    results = search_vector(db_session, course.id, query_embedding, limit=3)
    assert results[0] == chunks[0].id


def test_reciprocal_rank_fusion_combines_rankings():
    lexical = [1, 2, 3]
    vector = [2, 1, 4]
    fused = reciprocal_rank_fusion([lexical, vector])
    # chunk 1 and 2 both appear near the top of both lists, so should outrank 3/4
    assert fused[0] in (1, 2)
    assert fused[1] in (1, 2)
    assert set(fused) == {1, 2, 3, 4}
```

- [ ] **Step 6: Run test to verify it fails, then passes**

Run: `docker compose run --rm backend pytest tests/retrieval/test_lexical_vector_fusion.py -v`
Expected: FAIL with `ModuleNotFoundError` before Steps 1-4; `5 passed` after.

- [ ] **Step 7: Commit**

```bash
git add backend/app/retrieval/__init__.py backend/app/retrieval/lexical.py backend/app/retrieval/vector.py backend/app/retrieval/fusion.py backend/tests/retrieval/test_lexical_vector_fusion.py
git commit -m "feat: add lexical, vector, and RRF fusion retrieval"
```

---

## Task 10: Reranking, retrieval service, and debug search endpoint

**Files:**
- Create: `backend/app/retrieval/rerank.py`
- Create: `backend/app/retrieval/service.py`
- Create: `backend/app/routers/debug.py`
- Modify: `backend/app/main.py` (register debug router)
- Test: `backend/tests/retrieval/test_rerank_service.py`

**Interfaces:**
- Consumes: `search_lexical`, `search_vector`, `reciprocal_rank_fusion` (Task 9), `embedder.embed_query` (Task 5), `models.Chunk`.
- Produces: `rerank.rerank(query: str, candidates: list[Chunk], top_k: int) -> list[ScoredChunk]` where `ScoredChunk` is a dataclass (`chunk: Chunk`, `score: float`). Produces `service.retrieve(db: Session, course_id: int, query: str, top_k: int = 6) -> list[ScoredChunk]`. Router `GET /api/courses/{course_id}/search?q=...` returning per-leg + fused + reranked scores for tuning.

- [ ] **Step 1: Write `backend/app/retrieval/rerank.py`**

```python
from dataclasses import dataclass

from sentence_transformers import CrossEncoder

from app.models import Chunk

_MODEL = None


def _model() -> CrossEncoder:
    global _MODEL
    if _MODEL is None:
        _MODEL = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2", device="cpu")
    return _MODEL


@dataclass
class ScoredChunk:
    chunk: Chunk
    score: float


def rerank(query: str, candidates: list[Chunk], top_k: int) -> list[ScoredChunk]:
    if not candidates:
        return []

    pairs = [(query, f"{c.context_header}\n{c.text}" if c.context_header else c.text) for c in candidates]
    raw_scores = _model().predict(pairs)

    scored = [ScoredChunk(chunk=c, score=float(s)) for c, s in zip(candidates, raw_scores)]
    scored.sort(key=lambda sc: -sc.score)
    return scored[:top_k]
```

- [ ] **Step 2: Write `backend/app/retrieval/service.py`**

```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.embedder import embed_query
from app.models import Chunk
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.lexical import search_lexical
from app.retrieval.rerank import ScoredChunk, rerank
from app.retrieval.vector import search_vector

FUSED_CANDIDATES = 20
FINAL_TOP_K = 6


def retrieve(db: Session, course_id: int, query: str, top_k: int = FINAL_TOP_K) -> list[ScoredChunk]:
    lexical_ids = search_lexical(db, course_id, query, limit=50)
    query_embedding = embed_query(query)
    vector_ids = search_vector(db, course_id, query_embedding, limit=50)

    fused_ids = reciprocal_rank_fusion([lexical_ids, vector_ids])[:FUSED_CANDIDATES]
    if not fused_ids:
        return []

    chunks = db.scalars(select(Chunk).where(Chunk.id.in_(fused_ids))).all()
    chunks_by_id = {c.id: c for c in chunks}
    ordered_candidates = [chunks_by_id[cid] for cid in fused_ids if cid in chunks_by_id]

    return rerank(query, ordered_candidates, top_k=top_k)
```

- [ ] **Step 3: Write `backend/app/routers/debug.py`**

```python
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.ingestion.embedder import embed_query
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.lexical import search_lexical
from app.retrieval.rerank import rerank
from app.models import Chunk
from sqlalchemy import select

router = APIRouter(prefix="/api/courses", tags=["debug"])


@router.get("/{course_id}/search")
def debug_search(course_id: int, q: str, db: Session = Depends(get_db)):
    from app.retrieval.vector import search_vector

    lexical_ids = search_lexical(db, course_id, q, limit=50)
    vector_ids = search_vector(db, course_id, embed_query(q), limit=50)
    fused_ids = reciprocal_rank_fusion([lexical_ids, vector_ids])[:20]

    chunks = db.scalars(select(Chunk).where(Chunk.id.in_(fused_ids))).all()
    chunks_by_id = {c.id: c for c in chunks}
    ordered = [chunks_by_id[cid] for cid in fused_ids if cid in chunks_by_id]
    reranked = rerank(q, ordered, top_k=6)

    return {
        "lexical_rank": lexical_ids,
        "vector_rank": vector_ids,
        "fused_rank": fused_ids,
        "reranked": [{"chunk_id": sc.chunk.id, "score": sc.score, "text": sc.chunk.text} for sc in reranked],
    }
```

- [ ] **Step 4: Modify `backend/app/main.py`**

```python
from fastapi import FastAPI

from app.config import get_settings
from app.routers import courses, debug, documents

app = FastAPI(title="Study Notes Parser")
app.include_router(courses.router)
app.include_router(documents.router)
app.include_router(debug.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.on_event("startup")
def startup() -> None:
    get_settings()
```

- [ ] **Step 5: Write the failing test `backend/tests/retrieval/test_rerank_service.py`**

```python
from app.ingestion.embedder import embed_texts
from app.models import Chunk, Course, Document
from app.retrieval.rerank import rerank
from app.retrieval.service import retrieve


def _seed(db_session):
    course = Course(name="Rerank Test Course")
    db_session.add(course)
    db_session.flush()
    document = Document(
        course_id=course.id, original_filename="d.pdf", original_format="pdf",
        original_path="/tmp/d.pdf", file_sha256="e" * 64,
    )
    db_session.add(document)
    db_session.flush()

    texts = [
        "Mitochondria is the powerhouse of the cell and produces ATP through respiration.",
        "The Krebs cycle occurs in the mitochondrial matrix and generates electron carriers.",
        "The Renaissance was a period of cultural rebirth in Europe starting in Italy.",
    ]
    vectors = embed_texts(texts)
    chunks = []
    for i, (t, v) in enumerate(zip(texts, vectors)):
        chunk = Chunk(
            document_id=document.id, course_id=course.id, chunk_index=i, text=t,
            page_number=1, bboxes={"page_width": 612.0, "page_height": 792.0, "rects": []},
            token_count=12, embedding=v,
        )
        db_session.add(chunk)
        chunks.append(chunk)
    db_session.commit()
    return course, chunks


def test_rerank_orders_by_relevance(db_session):
    course, chunks = _seed(db_session)
    scored = rerank("what produces energy in the cell?", chunks, top_k=3)
    assert scored[0].chunk.id in (chunks[0].id, chunks[1].id)
    assert scored[-1].chunk.id == chunks[2].id


def test_retrieve_end_to_end_returns_relevant_chunks_first(db_session):
    course, chunks = _seed(db_session)
    results = retrieve(db_session, course.id, "how does the cell make energy?", top_k=2)
    assert len(results) == 2
    result_ids = {r.chunk.id for r in results}
    assert chunks[2].id not in result_ids
```

- [ ] **Step 6: Run test to verify it fails, then passes**

Run: `docker compose run --rm backend pytest tests/retrieval/test_rerank_service.py -v`
Expected: FAIL with `ModuleNotFoundError` before Steps 1-2; `2 passed` after (first run downloads the ~80MB reranker model into `hf_cache`).

- [ ] **Step 7: Manually verify the debug endpoint**

Run: `docker compose up -d` then upload a document via `POST /api/courses/{id}/documents`, wait for `ready`, then `curl "http://localhost:8000/api/courses/{id}/search?q=your+question"`.
Expected: JSON with `lexical_rank`, `vector_rank`, `fused_rank`, `reranked` populated.

- [ ] **Step 8: Commit**

```bash
git add backend/app/retrieval/rerank.py backend/app/retrieval/service.py backend/app/routers/debug.py backend/app/main.py backend/tests/retrieval/test_rerank_service.py
git commit -m "feat: add reranking, retrieval service, and debug search endpoint"
```

---

## Task 11: LLM provider abstraction

**Files:**
- Create: `backend/app/providers/__init__.py`
- Create: `backend/app/providers/base.py`
- Create: `backend/app/providers/anthropic_provider.py`
- Create: `backend/app/providers/openai_provider.py`
- Create: `backend/app/providers/factory.py`
- Test: `backend/tests/providers/test_providers.py`

**Interfaces:**
- Consumes: `config.get_settings` (Task 1, fields `llm_provider`, `llm_model`, `llm_api_key`, `llm_base_url`).
- Produces: `base.LLMMessage` (dataclass: `role: Literal["user","assistant"]`, `content: str`), `base.LLMResponse` (dataclass: `text: str`, `input_tokens: int | None`, `output_tokens: int | None`, `stop_reason: str | None`), `base.LLMProvider` (Protocol: `generate(messages, system=None, max_tokens=2048) -> LLMResponse`, `generate_stream(messages, system=None, max_tokens=2048) -> Iterator[str]`), `base.LLMProviderError`, `base.RateLimited`, `base.AuthError`. Produces `factory.get_provider() -> LLMProvider`.

- [ ] **Step 1: Write `backend/app/providers/__init__.py`** (empty file)

- [ ] **Step 2: Write `backend/app/providers/base.py`**

```python
from dataclasses import dataclass
from typing import Iterator, Literal, Protocol


@dataclass
class LLMMessage:
    role: Literal["user", "assistant"]
    content: str


@dataclass
class LLMResponse:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    stop_reason: str | None


class LLMProviderError(Exception):
    pass


class RateLimited(LLMProviderError):
    pass


class AuthError(LLMProviderError):
    pass


class LLMProvider(Protocol):
    def generate(
        self, messages: list[LLMMessage], system: str | None = None, max_tokens: int = 2048
    ) -> LLMResponse: ...

    def generate_stream(
        self, messages: list[LLMMessage], system: str | None = None, max_tokens: int = 2048
    ) -> Iterator[str]: ...
```

- [ ] **Step 3: Write `backend/app/providers/anthropic_provider.py`**

```python
from typing import Iterator

import anthropic

from app.providers.base import AuthError, LLMMessage, LLMProvider, LLMProviderError, LLMResponse, RateLimited


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, model: str):
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def _to_anthropic_messages(self, messages: list[LLMMessage]) -> list[dict]:
        return [{"role": m.role, "content": m.content} for m in messages]

    def generate(self, messages: list[LLMMessage], system: str | None = None, max_tokens: int = 2048) -> LLMResponse:
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system or anthropic.NOT_GIVEN,
                messages=self._to_anthropic_messages(messages),
            )
        except anthropic.AuthenticationError as exc:
            raise AuthError(str(exc)) from exc
        except anthropic.RateLimitError as exc:
            raise RateLimited(str(exc)) from exc
        except anthropic.APIError as exc:
            raise LLMProviderError(str(exc)) from exc

        text = "".join(block.text for block in response.content if block.type == "text")
        return LLMResponse(
            text=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            stop_reason=response.stop_reason,
        )

    def generate_stream(
        self, messages: list[LLMMessage], system: str | None = None, max_tokens: int = 2048
    ) -> Iterator[str]:
        try:
            with self._client.messages.stream(
                model=self._model,
                max_tokens=max_tokens,
                system=system or anthropic.NOT_GIVEN,
                messages=self._to_anthropic_messages(messages),
            ) as stream:
                yield from stream.text_stream
        except anthropic.AuthenticationError as exc:
            raise AuthError(str(exc)) from exc
        except anthropic.RateLimitError as exc:
            raise RateLimited(str(exc)) from exc
        except anthropic.APIError as exc:
            raise LLMProviderError(str(exc)) from exc
```

- [ ] **Step 4: Write `backend/app/providers/openai_provider.py`**

```python
from typing import Iterator

import openai

from app.providers.base import AuthError, LLMMessage, LLMProvider, LLMProviderError, LLMResponse, RateLimited


class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str, model: str, base_url: str | None = None):
        self._client = openai.OpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    def _to_openai_messages(self, messages: list[LLMMessage], system: str | None) -> list[dict]:
        result = []
        if system:
            result.append({"role": "system", "content": system})
        result.extend({"role": m.role, "content": m.content} for m in messages)
        return result

    def generate(self, messages: list[LLMMessage], system: str | None = None, max_tokens: int = 2048) -> LLMResponse:
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                max_tokens=max_tokens,
                messages=self._to_openai_messages(messages, system),
            )
        except openai.AuthenticationError as exc:
            raise AuthError(str(exc)) from exc
        except openai.RateLimitError as exc:
            raise RateLimited(str(exc)) from exc
        except openai.APIError as exc:
            raise LLMProviderError(str(exc)) from exc

        choice = response.choices[0]
        return LLMResponse(
            text=choice.message.content or "",
            input_tokens=response.usage.prompt_tokens if response.usage else None,
            output_tokens=response.usage.completion_tokens if response.usage else None,
            stop_reason=choice.finish_reason,
        )

    def generate_stream(
        self, messages: list[LLMMessage], system: str | None = None, max_tokens: int = 2048
    ) -> Iterator[str]:
        try:
            stream = self._client.chat.completions.create(
                model=self._model,
                max_tokens=max_tokens,
                messages=self._to_openai_messages(messages, system),
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except openai.AuthenticationError as exc:
            raise AuthError(str(exc)) from exc
        except openai.RateLimitError as exc:
            raise RateLimited(str(exc)) from exc
        except openai.APIError as exc:
            raise LLMProviderError(str(exc)) from exc
```

- [ ] **Step 5: Write `backend/app/providers/factory.py`**

```python
from app.config import get_settings
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import LLMProvider
from app.providers.openai_provider import OpenAIProvider

_REGISTRY = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
}


def get_provider() -> LLMProvider:
    settings = get_settings()
    provider_cls = _REGISTRY.get(settings.llm_provider)
    if provider_cls is None:
        raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider}")

    if settings.llm_provider == "openai":
        return OpenAIProvider(api_key=settings.llm_api_key, model=settings.llm_model, base_url=settings.llm_base_url)
    return AnthropicProvider(api_key=settings.llm_api_key, model=settings.llm_model)
```

- [ ] **Step 6: Write the failing test `backend/tests/providers/test_providers.py`**

```python
import os

import pytest

from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import LLMMessage


@pytest.mark.skipif(not os.environ.get("LLM_API_KEY"), reason="requires a real Anthropic API key")
def test_anthropic_generate_returns_text():
    provider = AnthropicProvider(api_key=os.environ["LLM_API_KEY"], model="claude-opus-4-8")
    response = provider.generate([LLMMessage(role="user", content="Say the word 'pong' and nothing else.")])
    assert "pong" in response.text.lower()
    assert response.stop_reason is not None


def test_anthropic_provider_implements_protocol():
    from app.providers.base import LLMProvider

    provider = AnthropicProvider(api_key="fake-key", model="claude-opus-4-8")
    assert isinstance(provider, LLMProvider)


def test_factory_returns_anthropic_provider_by_default(monkeypatch):
    from app.config import get_settings
    from app.providers.factory import get_provider

    get_settings.cache_clear()
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_API_KEY", "fake-key")
    monkeypatch.setenv("LLM_MODEL", "claude-opus-4-8")

    provider = get_provider()
    assert provider.__class__.__name__ == "AnthropicProvider"
    get_settings.cache_clear()
```

- [ ] **Step 7: Run test to verify it fails, then passes**

Run: `docker compose run --rm backend pytest tests/providers/test_providers.py -v`
Expected: FAIL with `ModuleNotFoundError` before Steps 1-5; after, `3 passed` (the real-API test auto-skips unless `LLM_API_KEY` is set in the environment when running pytest — this is expected and acceptable, it's a smoke test for manual verification, not part of CI-style runs).

- [ ] **Step 8: Commit**

```bash
git add backend/app/providers/ backend/tests/providers/test_providers.py
git commit -m "feat: add provider-agnostic LLM abstraction (Anthropic, OpenAI)"
```

---

## Task 12: Generation — prompt building and citation-aware chat service

**Files:**
- Create: `backend/app/generation/__init__.py`
- Create: `backend/app/generation/prompts.py`
- Create: `backend/app/generation/chat_service.py`
- Test: `backend/tests/generation/test_chat_service.py`

**Interfaces:**
- Consumes: `retrieval.service.retrieve`, `retrieval.rerank.ScoredChunk` (Task 10), `providers.base.LLMProvider`, `LLMMessage` (Task 11), `models.ChatMessage`, `models.ChatSession`, `models.Course`, `models.MessageCitation` (Task 2).
- Produces: `prompts.build_system_prompt(course_name: str, chunks: list[ScoredChunk]) -> tuple[str, dict[int, int]]` (marker number -> chunk id), `prompts.parse_citations(text: str, marker_map: dict[int, int]) -> list[int]` (ordered distinct marker numbers actually used and valid). Produces `chat_service.CitationInfo` dataclass (`marker, chunk_id, document_id, filename, page_number`) and `chat_service.stream_assistant_reply(db: Session, session: ChatSession, user_content: str, provider: LLMProvider) -> Iterator[tuple[str, dict]]` yielding `("delta", {"text": ...})` events during generation and a final `("done", {"message_id": int, "citations": [dict, ...]})` event, persisting the user message, assistant message, and citations along the way.

- [ ] **Step 1: Write `backend/app/generation/__init__.py`** (empty file)

- [ ] **Step 2: Write `backend/app/generation/prompts.py`**

```python
import re

from app.retrieval.rerank import ScoredChunk

_CITATION_PATTERN = re.compile(r"\[(\d+)\]")

_SYSTEM_TEMPLATE = """You are a study assistant. Answer ONLY from the provided excerpts of the \
user's course notes for "{course_name}". If the excerpts don't contain the answer, say so \
plainly — do not use outside knowledge for factual claims.

Cite your sources: after each claim, add the marker [n] where n is the excerpt number it came \
from. Use multiple markers [1][3] when a claim draws on several excerpts. Every factual sentence \
must carry at least one marker. Do not invent excerpt numbers; only 1 through {count} exist.

<excerpts>
{excerpts}
</excerpts>"""


def build_system_prompt(course_name: str, chunks: list[ScoredChunk]) -> tuple[str, dict[int, int]]:
    marker_map: dict[int, int] = {}
    excerpt_blocks = []
    for i, scored in enumerate(chunks, start=1):
        marker_map[i] = scored.chunk.id
        excerpt_blocks.append(
            f'[{i}] (from "{scored.chunk.document.original_filename}", page {scored.chunk.page_number})\n'
            f"{scored.chunk.text}"
        )

    system_prompt = _SYSTEM_TEMPLATE.format(
        course_name=course_name,
        count=len(chunks),
        excerpts="\n\n".join(excerpt_blocks) if excerpt_blocks else "(no relevant excerpts found)",
    )
    return system_prompt, marker_map


def parse_citations(text: str, marker_map: dict[int, int]) -> list[int]:
    used: list[int] = []
    for match in _CITATION_PATTERN.finditer(text):
        marker = int(match.group(1))
        if marker in marker_map and marker not in used:
            used.append(marker)
    return used
```

- [ ] **Step 3: Write `backend/app/generation/chat_service.py`**

```python
from dataclasses import asdict, dataclass
from typing import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.generation.prompts import build_system_prompt, parse_citations
from app.models import ChatMessage, ChatSession, Course, MessageCitation
from app.providers.base import LLMMessage, LLMProvider
from app.retrieval.service import retrieve


@dataclass
class CitationInfo:
    marker: int
    chunk_id: int
    document_id: int
    filename: str
    page_number: int


def _history_messages(db: Session, session_id: int) -> list[LLMMessage]:
    rows = db.scalars(
        select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.created_at)
    ).all()
    return [LLMMessage(role=r.role, content=r.content) for r in rows]


def stream_assistant_reply(
    db: Session, session: ChatSession, user_content: str, provider: LLMProvider
) -> Iterator[tuple[str, dict]]:
    course = db.get(Course, session.course_id)

    db.add(ChatMessage(session_id=session.id, role="user", content=user_content))
    db.commit()

    scored_chunks = retrieve(db, session.course_id, user_content)
    system_prompt, marker_map = build_system_prompt(course.name, scored_chunks)
    history = _history_messages(db, session.id)

    full_text = ""
    for delta in provider.generate_stream(history, system=system_prompt):
        full_text += delta
        yield "delta", {"text": delta}

    assistant_message = ChatMessage(session_id=session.id, role="assistant", content=full_text)
    db.add(assistant_message)
    db.flush()

    chunks_by_id = {sc.chunk.id: sc.chunk for sc in scored_chunks}
    used_markers = parse_citations(full_text, marker_map)

    citations: list[CitationInfo] = []
    for marker in used_markers:
        chunk_id = marker_map[marker]
        chunk = chunks_by_id[chunk_id]
        db.add(MessageCitation(message_id=assistant_message.id, chunk_id=chunk_id, marker_index=marker))
        citations.append(
            CitationInfo(
                marker=marker,
                chunk_id=chunk_id,
                document_id=chunk.document_id,
                filename=chunk.document.original_filename,
                page_number=chunk.page_number,
            )
        )

    db.commit()
    yield "done", {"message_id": assistant_message.id, "citations": [asdict(c) for c in citations]}
```

- [ ] **Step 4: Write the failing test `backend/tests/generation/test_chat_service.py`**

```python
from typing import Iterator

from app.generation.chat_service import stream_assistant_reply
from app.generation.prompts import build_system_prompt, parse_citations
from app.ingestion.embedder import embed_texts
from app.models import ChatSession, Chunk, Course, Document
from app.providers.base import LLMMessage


class FakeProvider:
    def __init__(self, reply_text: str):
        self._reply_text = reply_text

    def generate(self, messages, system=None, max_tokens=2048):
        raise NotImplementedError

    def generate_stream(self, messages: list[LLMMessage], system=None, max_tokens=2048) -> Iterator[str]:
        for word in self._reply_text.split(" "):
            yield word + " "


def _seed(db_session):
    course = Course(name="Cell Biology")
    db_session.add(course)
    db_session.flush()
    document = Document(
        course_id=course.id, original_filename="lecture1.pdf", original_format="pdf",
        original_path="/tmp/lecture1.pdf", file_sha256="f" * 64,
    )
    db_session.add(document)
    db_session.flush()

    texts = ["Mitochondria produce ATP through cellular respiration."]
    vectors = embed_texts(texts)
    chunk = Chunk(
        document_id=document.id, course_id=course.id, chunk_index=0, text=texts[0],
        page_number=3, bboxes={"page_width": 612.0, "page_height": 792.0, "rects": []},
        token_count=10, embedding=vectors[0],
    )
    db_session.add(chunk)
    db_session.flush()

    session = ChatSession(course_id=course.id)
    db_session.add(session)
    db_session.commit()
    return session, chunk


def test_parse_citations_extracts_valid_distinct_markers():
    marker_map = {1: 100, 2: 200}
    used = parse_citations("ATP is produced here [1]. Also true [2][1] and [9] is invalid.", marker_map)
    assert used == [1, 2]


def test_stream_assistant_reply_persists_messages_and_citations(db_session):
    session, chunk = _seed(db_session)
    provider = FakeProvider(f"Mitochondria produce ATP [1].")

    events = list(stream_assistant_reply(db_session, session, "What produces ATP?", provider))

    delta_events = [e for e in events if e[0] == "delta"]
    done_events = [e for e in events if e[0] == "done"]
    assert len(delta_events) > 0
    assert len(done_events) == 1

    done_data = done_events[0][1]
    assert len(done_data["citations"]) == 1
    assert done_data["citations"][0]["chunk_id"] == chunk.id
    assert done_data["citations"][0]["page_number"] == 3


def test_stream_assistant_reply_with_no_citations_in_reply(db_session):
    session, chunk = _seed(db_session)
    provider = FakeProvider("I'm not sure the notes cover this.")

    events = list(stream_assistant_reply(db_session, session, "Unrelated question?", provider))
    done_data = [e for e in events if e[0] == "done"][0][1]
    assert done_data["citations"] == []
```

- [ ] **Step 5: Run test to verify it fails, then passes**

Run: `docker compose run --rm backend pytest tests/generation/test_chat_service.py -v`
Expected: FAIL with `ModuleNotFoundError` before Steps 1-3; `3 passed` after.

- [ ] **Step 6: Commit**

```bash
git add backend/app/generation/ backend/tests/generation/test_chat_service.py
git commit -m "feat: add prompt construction and citation-aware chat service"
```

---

## Task 13: Chat API (sessions + streaming messages)

**Files:**
- Modify: `backend/app/schemas.py` (add chat schemas)
- Create: `backend/app/routers/chat.py`
- Modify: `backend/app/providers/factory.py` (make `get_provider` usable as a FastAPI dependency — already a plain callable, no change needed, referenced here for clarity)
- Modify: `backend/app/main.py` (register router)
- Test: `backend/tests/routers/test_chat.py`

**Interfaces:**
- Consumes: `chat_service.stream_assistant_reply` (Task 12), `providers.factory.get_provider` (Task 11), `models.ChatSession`, `models.ChatMessage`, `models.MessageCitation`.
- Produces: `schemas.ChatSessionOut`, `schemas.ChatMessageCreate`, `schemas.CitationOut`, `schemas.ChatMessageOut`. Router: `POST /api/courses/{course_id}/sessions`, `GET /api/courses/{course_id}/sessions`, `GET /api/sessions/{id}/messages`, `POST /api/sessions/{id}/messages` (SSE), `DELETE /api/sessions/{id}`.

- [ ] **Step 1: Add to `backend/app/schemas.py`**

```python
class ChatSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    course_id: int
    title: str | None
    created_at: datetime


class ChatMessageCreate(BaseModel):
    content: str


class CitationOut(BaseModel):
    marker: int
    chunk_id: int
    document_id: int
    filename: str
    page_number: int


class ChatMessageOut(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime
    citations: list[CitationOut] = []
```

- [ ] **Step 2: Write `backend/app/routers/chat.py`**

```python
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.generation.chat_service import stream_assistant_reply
from app.models import ChatMessage, ChatSession, Course, MessageCitation
from app.providers.base import LLMProvider
from app.providers.factory import get_provider
from app.schemas import ChatMessageCreate, ChatMessageOut, ChatSessionOut, CitationOut

router = APIRouter(tags=["chat"])


@router.post("/api/courses/{course_id}/sessions", response_model=ChatSessionOut, status_code=201)
def create_session(course_id: int, db: Session = Depends(get_db)):
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    session = ChatSession(course_id=course_id)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


@router.get("/api/courses/{course_id}/sessions", response_model=list[ChatSessionOut])
def list_sessions(course_id: int, db: Session = Depends(get_db)):
    return db.scalars(
        select(ChatSession).where(ChatSession.course_id == course_id).order_by(ChatSession.created_at)
    ).all()


@router.get("/api/sessions/{session_id}/messages", response_model=list[ChatMessageOut])
def get_messages(session_id: int, db: Session = Depends(get_db)):
    messages = db.scalars(
        select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.created_at)
    ).all()

    result = []
    for m in messages:
        citation_rows = db.scalars(select(MessageCitation).where(MessageCitation.message_id == m.id)).all()
        citations = [
            CitationOut(
                marker=c.marker_index,
                chunk_id=c.chunk_id,
                document_id=c.chunk.document_id,
                filename=c.chunk.document.original_filename,
                page_number=c.chunk.page_number,
            )
            for c in citation_rows
        ]
        result.append(ChatMessageOut(id=m.id, role=m.role, content=m.content, created_at=m.created_at, citations=citations))
    return result


@router.post("/api/sessions/{session_id}/messages")
def post_message(
    session_id: int,
    payload: ChatMessageCreate,
    db: Session = Depends(get_db),
    provider: LLMProvider = Depends(get_provider),
):
    session = db.get(ChatSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    def event_stream():
        for event_type, data in stream_assistant_reply(db, session, payload.content, provider):
            yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.delete("/api/sessions/{session_id}", status_code=204)
def delete_session(session_id: int, db: Session = Depends(get_db)):
    session = db.get(ChatSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    db.delete(session)
    db.commit()
```

- [ ] **Step 3: Modify `backend/app/main.py`**

```python
from fastapi import FastAPI

from app.config import get_settings
from app.routers import chat, courses, debug, documents

app = FastAPI(title="Study Notes Parser")
app.include_router(courses.router)
app.include_router(documents.router)
app.include_router(debug.router)
app.include_router(chat.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.on_event("startup")
def startup() -> None:
    get_settings()
```

- [ ] **Step 4: Write the failing test `backend/tests/routers/test_chat.py`**

```python
import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.ingestion.embedder import embed_texts
from app.main import app
from app.models import Chunk, Course, Document
from app.providers.factory import get_provider


class FakeProvider:
    def generate(self, messages, system=None, max_tokens=2048):
        raise NotImplementedError

    def generate_stream(self, messages, system=None, max_tokens=2048):
        yield "Mitochondria produce ATP [1]. "


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_provider] = lambda: FakeProvider()
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def course_with_chunk(db_session):
    course = Course(name="Chat Test Course")
    db_session.add(course)
    db_session.flush()
    document = Document(
        course_id=course.id, original_filename="notes.pdf", original_format="pdf",
        original_path="/tmp/notes.pdf", file_sha256="9" * 64,
    )
    db_session.add(document)
    db_session.flush()
    vectors = embed_texts(["Mitochondria produce ATP through cellular respiration."])
    chunk = Chunk(
        document_id=document.id, course_id=course.id, chunk_index=0,
        text="Mitochondria produce ATP through cellular respiration.",
        page_number=1, bboxes={"page_width": 612.0, "page_height": 792.0, "rects": []},
        token_count=10, embedding=vectors[0],
    )
    db_session.add(chunk)
    db_session.commit()
    return course


def test_create_session_and_send_message(client, course_with_chunk):
    session_resp = client.post(f"/api/courses/{course_with_chunk.id}/sessions")
    assert session_resp.status_code == 201
    session_id = session_resp.json()["id"]

    message_resp = client.post(f"/api/sessions/{session_id}/messages", json={"content": "What produces ATP?"})
    assert message_resp.status_code == 200
    assert "event: delta" in message_resp.text
    assert "event: done" in message_resp.text

    messages_resp = client.get(f"/api/sessions/{session_id}/messages")
    messages = messages_resp.json()
    assert len(messages) == 2  # user + assistant
    assistant_message = messages[1]
    assert assistant_message["role"] == "assistant"
    assert len(assistant_message["citations"]) == 1
    assert assistant_message["citations"][0]["page_number"] == 1


def test_delete_session(client, course_with_chunk):
    session_id = client.post(f"/api/courses/{course_with_chunk.id}/sessions").json()["id"]
    response = client.delete(f"/api/sessions/{session_id}")
    assert response.status_code == 204
```

- [ ] **Step 5: Run test to verify it fails, then passes**

Run: `docker compose run --rm backend pytest tests/routers/test_chat.py -v`
Expected: FAIL with `ModuleNotFoundError`/`AttributeError` before Steps 1-3; `2 passed` after.

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas.py backend/app/routers/chat.py backend/app/main.py backend/tests/routers/test_chat.py
git commit -m "feat: add chat sessions and streaming messages API"
```

---

## Task 14: Chunk detail API for the source panel

**Files:**
- Create: `backend/app/routers/chunks.py`
- Modify: `backend/app/main.py` (register router)
- Test: `backend/tests/routers/test_chunks.py`

**Interfaces:**
- Consumes: `models.Chunk`.
- Produces: `GET /api/chunks/{chunk_id}` returning `{chunk_id, document_id, filename, pdf_url, page_number, bboxes, text, context_header}`.

- [ ] **Step 1: Write `backend/app/routers/chunks.py`**

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Chunk

router = APIRouter(prefix="/api/chunks", tags=["chunks"])


@router.get("/{chunk_id}")
def get_chunk(chunk_id: int, db: Session = Depends(get_db)):
    chunk = db.get(Chunk, chunk_id)
    if chunk is None:
        raise HTTPException(status_code=404, detail="Chunk not found")
    return {
        "chunk_id": chunk.id,
        "document_id": chunk.document_id,
        "filename": chunk.document.original_filename,
        "pdf_url": f"/api/documents/{chunk.document_id}/pdf",
        "page_number": chunk.page_number,
        "bboxes": chunk.bboxes,
        "text": chunk.text,
        "context_header": chunk.context_header,
    }
```

- [ ] **Step 2: Modify `backend/app/main.py`**

```python
from fastapi import FastAPI

from app.config import get_settings
from app.routers import chat, chunks, courses, debug, documents

app = FastAPI(title="Study Notes Parser")
app.include_router(courses.router)
app.include_router(documents.router)
app.include_router(debug.router)
app.include_router(chat.router)
app.include_router(chunks.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.on_event("startup")
def startup() -> None:
    get_settings()
```

- [ ] **Step 3: Write the failing test `backend/tests/routers/test_chunks.py`**

```python
import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.ingestion.embedder import embed_texts
from app.main import app
from app.models import Chunk, Course, Document


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_get_chunk_returns_source_panel_data(db_session, client):
    course = Course(name="Chunk Test Course")
    db_session.add(course)
    db_session.flush()
    document = Document(
        course_id=course.id, original_filename="week2.pdf", original_format="pdf",
        original_path="/tmp/week2.pdf", pdf_path="/tmp/week2.pdf", file_sha256="8" * 64,
    )
    db_session.add(document)
    db_session.flush()
    vectors = embed_texts(["Cellular respiration text."])
    chunk = Chunk(
        document_id=document.id, course_id=course.id, chunk_index=0,
        text="Cellular respiration text.", page_number=4,
        bboxes={"page_width": 612.0, "page_height": 792.0, "rects": [{"x0": 1, "y0": 2, "x1": 3, "y1": 4}]},
        token_count=5, embedding=vectors[0],
    )
    db_session.add(chunk)
    db_session.commit()

    response = client.get(f"/api/chunks/{chunk.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["filename"] == "week2.pdf"
    assert body["page_number"] == 4
    assert body["pdf_url"] == f"/api/documents/{document.id}/pdf"
    assert body["bboxes"]["rects"][0]["x1"] == 3


def test_get_missing_chunk_returns_404(client):
    response = client.get("/api/chunks/999999")
    assert response.status_code == 404
```

- [ ] **Step 4: Run test to verify it fails, then passes**

Run: `docker compose run --rm backend pytest tests/routers/test_chunks.py -v`
Expected: FAIL with `ModuleNotFoundError` before Steps 1-2; `2 passed` after.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/chunks.py backend/app/main.py backend/tests/routers/test_chunks.py
git commit -m "feat: add chunk detail API for the source panel"
```

---

## Task 15: Frontend scaffolding (Vite + React + TypeScript + TanStack Query)

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/tsconfig.json`
- Create: `frontend/tsconfig.node.json`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/App.tsx`
- Create: `frontend/Dockerfile`
- Create: `frontend/src/api/client.ts`
- Test: `frontend/src/App.test.tsx`
- Create: `frontend/vitest.config.ts`

**Interfaces:**
- Produces: `api/client.ts` exporting `apiFetch<T>(path: string, options?: RequestInit) -> Promise<T>` (fetch wrapper, `baseURL` empty since Vite proxies `/api`). Produces `App.tsx` default export rendering a root layout with a `QueryClientProvider`.

- [ ] **Step 1: Write `frontend/package.json`**

```json
{
  "name": "notes-parser-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "test": "vitest run"
  },
  "dependencies": {
    "@tanstack/react-query": "^5.51.0",
    "pdfjs-dist": "^4.5.0",
    "react": "^18.3.0",
    "react-dom": "^18.3.0"
  },
  "devDependencies": {
    "@testing-library/jest-dom": "^6.4.0",
    "@testing-library/react": "^16.0.0",
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.0",
    "jsdom": "^24.1.0",
    "typescript": "^5.5.0",
    "vite": "^5.3.0",
    "vitest": "^2.0.0"
  }
}
```

- [ ] **Step 2: Write `frontend/vite.config.ts`**

```typescript
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": {
        target: "http://backend:8000",
        changeOrigin: true,
      },
    },
  },
});
```

- [ ] **Step 3: Write `frontend/vitest.config.ts`**

```typescript
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test-setup.ts",
  },
});
```

- [ ] **Step 4: Write `frontend/src/test-setup.ts`**

```typescript
import "@testing-library/jest-dom";
```

- [ ] **Step 5: Write `frontend/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "useDefineForClassFields": true,
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "types": ["vitest/globals", "@testing-library/jest-dom"]
  },
  "include": ["src"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

- [ ] **Step 6: Write `frontend/tsconfig.node.json`**

```json
{
  "compilerOptions": {
    "composite": true,
    "skipLibCheck": true,
    "module": "ESNext",
    "moduleResolution": "bundler"
  },
  "include": ["vite.config.ts", "vitest.config.ts"]
}
```

- [ ] **Step 7: Write `frontend/index.html`**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <title>Study Notes Parser</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 8: Write `frontend/src/api/client.ts`**

```typescript
export async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, options);
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`Request to ${path} failed with ${response.status}: ${body}`);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}
```

- [ ] **Step 9: Write `frontend/src/App.tsx`**

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const queryClient = new QueryClient();

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <div className="app-root">
        <h1>Study Notes Parser</h1>
      </div>
    </QueryClientProvider>
  );
}
```

- [ ] **Step 10: Write `frontend/src/main.tsx`**

```tsx
import React from "react";
import ReactDOM from "react-dom/client";

import App from "./App";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
```

- [ ] **Step 11: Write `frontend/Dockerfile`**

```dockerfile
FROM node:22-slim

WORKDIR /app
COPY package.json .
RUN npm install

COPY . .

CMD ["npm", "run", "dev", "--", "--host"]
```

- [ ] **Step 12: Write the failing test `frontend/src/App.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import App from "./App";

describe("App", () => {
  it("renders the app title", () => {
    render(<App />);
    expect(screen.getByText("Study Notes Parser")).toBeInTheDocument();
  });
});
```

- [ ] **Step 13: Run test to verify it fails, then passes**

Run: `docker compose run --rm frontend npm install` then `docker compose run --rm frontend npm run test`
Expected: before Steps 1-11 exist, the run fails outright (no `package.json`); after, `1 passed`.

- [ ] **Step 14: Commit**

```bash
git add frontend/
git commit -m "feat: scaffold Vite + React + TypeScript frontend with TanStack Query"
```

---

## Task 16: Course selector and API hooks

**Files:**
- Create: `frontend/src/api/courses.ts`
- Create: `frontend/src/components/courses/CourseSelector.tsx`
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/components/courses/CourseSelector.test.tsx`

**Interfaces:**
- Consumes: `apiFetch` (Task 15).
- Produces: `api/courses.ts` exporting `Course` type (`{id, name, created_at, document_count}`), `useCourses()`, `useCreateCourse()`, `useDeleteCourse()` (TanStack Query hooks). Produces `CourseSelector` component with props `{selectedCourseId: number | null, onSelect: (id: number) => void}`.

- [ ] **Step 1: Write `frontend/src/api/courses.ts`**

```typescript
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "./client";

export interface Course {
  id: number;
  name: string;
  created_at: string;
  document_count: number;
}

export function useCourses() {
  return useQuery({
    queryKey: ["courses"],
    queryFn: () => apiFetch<Course[]>("/api/courses"),
  });
}

export function useCreateCourse() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      apiFetch<Course>("/api/courses", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["courses"] }),
  });
}

export function useDeleteCourse() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => apiFetch<void>(`/api/courses/${id}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["courses"] }),
  });
}
```

- [ ] **Step 2: Write `frontend/src/components/courses/CourseSelector.tsx`**

```tsx
import { useState } from "react";

import { useCourses, useCreateCourse } from "../../api/courses";

interface CourseSelectorProps {
  selectedCourseId: number | null;
  onSelect: (id: number) => void;
}

export function CourseSelector({ selectedCourseId, onSelect }: CourseSelectorProps) {
  const { data: courses, isLoading } = useCourses();
  const createCourse = useCreateCourse();
  const [newCourseName, setNewCourseName] = useState("");

  if (isLoading) {
    return <div>Loading courses...</div>;
  }

  const handleCreate = () => {
    const name = newCourseName.trim();
    if (!name) return;
    createCourse.mutate(name, {
      onSuccess: (course) => {
        setNewCourseName("");
        onSelect(course.id);
      },
    });
  };

  return (
    <div className="course-selector">
      <ul>
        {(courses ?? []).map((course) => (
          <li key={course.id}>
            <button
              aria-pressed={course.id === selectedCourseId}
              onClick={() => onSelect(course.id)}
            >
              {course.name} ({course.document_count})
            </button>
          </li>
        ))}
      </ul>
      <input
        aria-label="New course name"
        value={newCourseName}
        onChange={(e) => setNewCourseName(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && handleCreate()}
      />
      <button onClick={handleCreate}>Add course</button>
    </div>
  );
}
```

- [ ] **Step 3: Modify `frontend/src/App.tsx`**

```tsx
import { useState } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { CourseSelector } from "./components/courses/CourseSelector";

const queryClient = new QueryClient();

export default function App() {
  const [selectedCourseId, setSelectedCourseId] = useState<number | null>(null);

  return (
    <QueryClientProvider client={queryClient}>
      <div className="app-root">
        <h1>Study Notes Parser</h1>
        <CourseSelector selectedCourseId={selectedCourseId} onSelect={setSelectedCourseId} />
      </div>
    </QueryClientProvider>
  );
}
```

- [ ] **Step 4: Write the failing test `frontend/src/components/courses/CourseSelector.test.tsx`**

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CourseSelector } from "./CourseSelector";

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("CourseSelector", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url === "/api/courses") {
          return new Response(
            JSON.stringify([{ id: 1, name: "Biology", created_at: "2026-01-01T00:00:00Z", document_count: 2 }]),
            { status: 200, headers: { "Content-Type": "application/json" } }
          );
        }
        throw new Error(`Unexpected fetch to ${url}`);
      })
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders courses returned from the API", async () => {
    renderWithClient(<CourseSelector selectedCourseId={null} onSelect={() => {}} />);
    await waitFor(() => expect(screen.getByText("Biology (2)")).toBeInTheDocument());
  });

  it("calls onSelect when a course button is clicked", async () => {
    const onSelect = vi.fn();
    renderWithClient(<CourseSelector selectedCourseId={null} onSelect={onSelect} />);
    const button = await screen.findByText("Biology (2)");
    button.click();
    expect(onSelect).toHaveBeenCalledWith(1);
  });
});
```

- [ ] **Step 5: Run test to verify it fails, then passes**

Run: `docker compose run --rm frontend npm run test`
Expected: FAIL before Steps 1-2 with a module-not-found error; `2 passed` after (plus the existing `App.test.tsx` — 3 total).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/courses.ts frontend/src/components/courses/ frontend/src/App.tsx
git commit -m "feat: add course selector with create/list functionality"
```

---

## Task 17: Document upload and status list

**Files:**
- Create: `frontend/src/api/documents.ts`
- Create: `frontend/src/components/documents/UploadDropzone.tsx`
- Create: `frontend/src/components/documents/DocumentList.tsx`
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/components/documents/DocumentList.test.tsx`

**Interfaces:**
- Consumes: `apiFetch` (Task 15).
- Produces: `api/documents.ts` exporting `Document` type (`{id, course_id, original_filename, original_format, ingest_status, ingest_error, page_count, created_at}`), `useDocuments(courseId: number)` (polls every 1500ms while any document is not `ready`/`failed`), `useUploadDocuments(courseId: number)`, `useRetryDocument()`. Produces `UploadDropzone` (`{courseId: number}`) and `DocumentList` (`{courseId: number}`) components.

- [ ] **Step 1: Write `frontend/src/api/documents.ts`**

```typescript
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "./client";

export interface Document {
  id: number;
  course_id: number;
  original_filename: string;
  original_format: string;
  ingest_status: "pending" | "converting" | "parsing" | "embedding" | "ready" | "failed";
  ingest_error: string | null;
  page_count: number | null;
  created_at: string;
}

export function useDocuments(courseId: number) {
  return useQuery({
    queryKey: ["documents", courseId],
    queryFn: () => apiFetch<Document[]>(`/api/courses/${courseId}/documents`),
    refetchInterval: (query) => {
      const docs = query.state.data as Document[] | undefined;
      const stillIngesting = docs?.some((d) => !["ready", "failed"].includes(d.ingest_status));
      return stillIngesting ? 1500 : false;
    },
  });
}

export function useUploadDocuments(courseId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (files: FileList) => {
      const formData = new FormData();
      Array.from(files).forEach((file) => formData.append("files", file));
      return apiFetch<Document[]>(`/api/courses/${courseId}/documents`, {
        method: "POST",
        body: formData,
      });
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["documents", courseId] }),
  });
}

export function useRetryDocument(courseId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (documentId: number) => apiFetch<Document>(`/api/documents/${documentId}/retry`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["documents", courseId] }),
  });
}
```

- [ ] **Step 2: Write `frontend/src/components/documents/UploadDropzone.tsx`**

```tsx
import { useRef } from "react";

import { useUploadDocuments } from "../../api/documents";

interface UploadDropzoneProps {
  courseId: number;
}

export function UploadDropzone({ courseId }: UploadDropzoneProps) {
  const upload = useUploadDocuments(courseId);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFiles = (files: FileList | null) => {
    if (files && files.length > 0) {
      upload.mutate(files);
    }
  };

  return (
    <div
      className="upload-dropzone"
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        handleFiles(e.dataTransfer.files);
      }}
      onClick={() => inputRef.current?.click()}
    >
      <input
        ref={inputRef}
        type="file"
        multiple
        accept=".pdf,.docx,.pptx"
        aria-label="Upload notes"
        style={{ display: "none" }}
        onChange={(e) => handleFiles(e.target.files)}
      />
      <p>Drop PDF, DOCX, or PPTX files here, or click to select.</p>
    </div>
  );
}
```

- [ ] **Step 3: Write `frontend/src/components/documents/DocumentList.tsx`**

```tsx
import { useDocuments, useRetryDocument } from "../../api/documents";

interface DocumentListProps {
  courseId: number;
}

export function DocumentList({ courseId }: DocumentListProps) {
  const { data: documents, isLoading } = useDocuments(courseId);
  const retry = useRetryDocument(courseId);

  if (isLoading) {
    return <div>Loading documents...</div>;
  }

  return (
    <ul className="document-list">
      {(documents ?? []).map((doc) => (
        <li key={doc.id}>
          <span>{doc.original_filename}</span>
          <span className={`status-chip status-${doc.ingest_status}`}>{doc.ingest_status}</span>
          {doc.ingest_status === "failed" && (
            <button onClick={() => retry.mutate(doc.id)}>Retry</button>
          )}
        </li>
      ))}
    </ul>
  );
}
```

- [ ] **Step 4: Modify `frontend/src/App.tsx`**

```tsx
import { useState } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { CourseSelector } from "./components/courses/CourseSelector";
import { DocumentList } from "./components/documents/DocumentList";
import { UploadDropzone } from "./components/documents/UploadDropzone";

const queryClient = new QueryClient();

export default function App() {
  const [selectedCourseId, setSelectedCourseId] = useState<number | null>(null);

  return (
    <QueryClientProvider client={queryClient}>
      <div className="app-root">
        <h1>Study Notes Parser</h1>
        <CourseSelector selectedCourseId={selectedCourseId} onSelect={setSelectedCourseId} />
        {selectedCourseId !== null && (
          <>
            <UploadDropzone courseId={selectedCourseId} />
            <DocumentList courseId={selectedCourseId} />
          </>
        )}
      </div>
    </QueryClientProvider>
  );
}
```

- [ ] **Step 5: Write the failing test `frontend/src/components/documents/DocumentList.test.tsx`**

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DocumentList } from "./DocumentList";

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("DocumentList", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify([
            {
              id: 1,
              course_id: 1,
              original_filename: "week1.pdf",
              original_format: "pdf",
              ingest_status: "ready",
              ingest_error: null,
              page_count: 3,
              created_at: "2026-01-01T00:00:00Z",
            },
            {
              id: 2,
              course_id: 1,
              original_filename: "week2.docx",
              original_format: "docx",
              ingest_status: "failed",
              ingest_error: "conversion failed",
              page_count: null,
              created_at: "2026-01-01T00:00:00Z",
            },
          ]),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      )
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders documents with status chips and a retry button for failed ones", async () => {
    renderWithClient(<DocumentList courseId={1} />);
    await waitFor(() => expect(screen.getByText("week1.pdf")).toBeInTheDocument());
    expect(screen.getByText("ready")).toBeInTheDocument();
    expect(screen.getByText("week2.docx")).toBeInTheDocument();
    expect(screen.getByText("failed")).toBeInTheDocument();
    expect(screen.getByText("Retry")).toBeInTheDocument();
  });
});
```

- [ ] **Step 6: Run test to verify it fails, then passes**

Run: `docker compose run --rm frontend npm run test`
Expected: FAIL before Steps 1-3 with module-not-found; `4 passed` total after (App, CourseSelector x2, DocumentList).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/documents.ts frontend/src/components/documents/ frontend/src/App.tsx
git commit -m "feat: add document upload dropzone and status list"
```

---

## Task 18: Chat pane with streaming and citation chips

**Files:**
- Create: `frontend/src/api/chat.ts`
- Create: `frontend/src/components/chat/ChatPane.tsx`
- Create: `frontend/src/components/chat/MessageList.tsx`
- Create: `frontend/src/components/chat/ChatInput.tsx`
- Create: `frontend/src/components/chat/CitationChip.tsx`
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/components/chat/MessageList.test.tsx`
- Test: `frontend/src/components/chat/CitationChip.test.tsx`

**Interfaces:**
- Consumes: `apiFetch` (Task 15).
- Produces: `api/chat.ts` exporting `ChatMessage` type (`{id, role, content, created_at, citations: Citation[]}`), `Citation` type (`{marker, chunk_id, document_id, filename, page_number}`), `useChatSessions(courseId)`, `useCreateChatSession(courseId)`, `useChatMessages(sessionId)`, and `sendMessageStream(sessionId, content, onDelta, onDone)` (a plain async function using `fetch` + manual SSE parsing, not a query hook, since streaming writes need to update local component state incrementally). Produces `CitationChip` component with props `{citation: Citation, onOpenSource: (chunkId: number) => void}` rendering a clickable `[n]` badge. Produces `MessageList` with props `{messages: ChatMessage[], onOpenSource: (chunkId: number) => void}` that renders each message's content, replacing `[n]` substrings with `CitationChip` components resolved against that message's `citations` array. Produces `ChatPane` with props `{courseId: number, onOpenSource: (chunkId: number) => void}`.

- [ ] **Step 1: Write `frontend/src/api/chat.ts`**

```typescript
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "./client";

export interface Citation {
  marker: number;
  chunk_id: number;
  document_id: number;
  filename: string;
  page_number: number;
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  citations: Citation[];
}

export interface ChatSession {
  id: number;
  course_id: number;
  title: string | null;
  created_at: string;
}

export function useChatSessions(courseId: number) {
  return useQuery({
    queryKey: ["chat-sessions", courseId],
    queryFn: () => apiFetch<ChatSession[]>(`/api/courses/${courseId}/sessions`),
  });
}

export function useCreateChatSession(courseId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch<ChatSession>(`/api/courses/${courseId}/sessions`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["chat-sessions", courseId] }),
  });
}

export function useChatMessages(sessionId: number | null) {
  return useQuery({
    queryKey: ["chat-messages", sessionId],
    queryFn: () => apiFetch<ChatMessage[]>(`/api/sessions/${sessionId}/messages`),
    enabled: sessionId !== null,
  });
}

export async function sendMessageStream(
  sessionId: number,
  content: string,
  onDelta: (text: string) => void,
  onDone: (data: { message_id: number; citations: Citation[] }) => void
): Promise<void> {
  const response = await fetch(`/api/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!response.body) throw new Error("No response body for streaming message");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";

    for (const rawEvent of events) {
      const lines = rawEvent.split("\n");
      const eventLine = lines.find((l) => l.startsWith("event: "));
      const dataLine = lines.find((l) => l.startsWith("data: "));
      if (!eventLine || !dataLine) continue;

      const eventType = eventLine.slice("event: ".length);
      const data = JSON.parse(dataLine.slice("data: ".length));

      if (eventType === "delta") onDelta(data.text);
      if (eventType === "done") onDone(data);
    }
  }
}
```

- [ ] **Step 2: Write `frontend/src/components/chat/CitationChip.tsx`**

```tsx
import type { Citation } from "../../api/chat";

interface CitationChipProps {
  citation: Citation;
  onOpenSource: (chunkId: number) => void;
}

export function CitationChip({ citation, onOpenSource }: CitationChipProps) {
  return (
    <button
      className="citation-chip"
      title={`${citation.filename}, page ${citation.page_number}`}
      onClick={() => onOpenSource(citation.chunk_id)}
    >
      [{citation.marker}]
    </button>
  );
}
```

- [ ] **Step 3: Write `frontend/src/components/chat/MessageList.tsx`**

```tsx
import type { ReactNode } from "react";

import type { ChatMessage } from "../../api/chat";
import { CitationChip } from "./CitationChip";

interface MessageListProps {
  messages: ChatMessage[];
  onOpenSource: (chunkId: number) => void;
}

function renderContentWithCitations(message: ChatMessage, onOpenSource: (chunkId: number) => void): ReactNode[] {
  const citationsByMarker = new Map(message.citations.map((c) => [c.marker, c]));
  const parts = message.content.split(/(\[\d+\])/g);

  return parts.map((part, index) => {
    const match = part.match(/^\[(\d+)\]$/);
    if (match) {
      const marker = Number(match[1]);
      const citation = citationsByMarker.get(marker);
      if (citation) {
        return <CitationChip key={index} citation={citation} onOpenSource={onOpenSource} />;
      }
    }
    return <span key={index}>{part}</span>;
  });
}

export function MessageList({ messages, onOpenSource }: MessageListProps) {
  return (
    <div className="message-list">
      {messages.map((message) => (
        <div key={message.id} className={`message message-${message.role}`}>
          {renderContentWithCitations(message, onOpenSource)}
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Write `frontend/src/components/chat/ChatInput.tsx`**

```tsx
import { useState } from "react";

interface ChatInputProps {
  onSend: (content: string) => void;
  disabled: boolean;
}

export function ChatInput({ onSend, disabled }: ChatInputProps) {
  const [value, setValue] = useState("");

  const handleSend = () => {
    const trimmed = value.trim();
    if (!trimmed) return;
    onSend(trimmed);
    setValue("");
  };

  return (
    <div className="chat-input">
      <input
        aria-label="Chat message"
        value={value}
        disabled={disabled}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && handleSend()}
      />
      <button onClick={handleSend} disabled={disabled}>
        Send
      </button>
    </div>
  );
}
```

- [ ] **Step 5: Write `frontend/src/components/chat/ChatPane.tsx`**

```tsx
import { useEffect, useState } from "react";

import { useQueryClient } from "@tanstack/react-query";

import type { ChatMessage } from "../../api/chat";
import { sendMessageStream, useChatMessages, useChatSessions, useCreateChatSession } from "../../api/chat";
import { ChatInput } from "./ChatInput";
import { MessageList } from "./MessageList";

interface ChatPaneProps {
  courseId: number;
  onOpenSource: (chunkId: number) => void;
}

export function ChatPane({ courseId, onOpenSource }: ChatPaneProps) {
  const { data: sessions } = useChatSessions(courseId);
  const createSession = useCreateChatSession(courseId);
  const [sessionId, setSessionId] = useState<number | null>(null);
  const { data: persistedMessages } = useChatMessages(sessionId);
  const [streamingMessages, setStreamingMessages] = useState<ChatMessage[]>([]);
  const [isSending, setIsSending] = useState(false);
  const queryClient = useQueryClient();

  useEffect(() => {
    if (sessions && sessions.length > 0 && sessionId === null) {
      setSessionId(sessions[0].id);
    }
  }, [sessions, sessionId]);

  const handleStartSession = () => {
    createSession.mutate(undefined, { onSuccess: (session) => setSessionId(session.id) });
  };

  const handleSend = async (content: string) => {
    if (sessionId === null) return;
    setIsSending(true);

    const userMessage: ChatMessage = {
      id: -1,
      role: "user",
      content,
      created_at: new Date().toISOString(),
      citations: [],
    };
    const assistantDraft: ChatMessage = {
      id: -2,
      role: "assistant",
      content: "",
      created_at: new Date().toISOString(),
      citations: [],
    };
    setStreamingMessages([userMessage, assistantDraft]);

    await sendMessageStream(
      sessionId,
      content,
      (delta) => {
        setStreamingMessages((prev) => {
          const [user, assistant] = prev;
          return [user, { ...assistant, content: assistant.content + delta }];
        });
      },
      (data) => {
        setStreamingMessages((prev) => {
          const [user, assistant] = prev;
          return [user, { ...assistant, id: data.message_id, citations: data.citations }];
        });
        queryClient.invalidateQueries({ queryKey: ["chat-messages", sessionId] });
        setIsSending(false);
      }
    );
  };

  if (sessionId === null) {
    return (
      <div className="chat-pane">
        <button onClick={handleStartSession}>Start a new chat</button>
      </div>
    );
  }

  const allMessages = [...(persistedMessages ?? []), ...(isSending ? streamingMessages : [])];

  return (
    <div className="chat-pane">
      <MessageList messages={allMessages} onOpenSource={onOpenSource} />
      <ChatInput onSend={handleSend} disabled={isSending} />
    </div>
  );
}
```

- [ ] **Step 6: Modify `frontend/src/App.tsx`**

```tsx
import { useState } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ChatPane } from "./components/chat/ChatPane";
import { CourseSelector } from "./components/courses/CourseSelector";
import { DocumentList } from "./components/documents/DocumentList";
import { UploadDropzone } from "./components/documents/UploadDropzone";

const queryClient = new QueryClient();

export default function App() {
  const [selectedCourseId, setSelectedCourseId] = useState<number | null>(null);
  const [openChunkId, setOpenChunkId] = useState<number | null>(null);

  return (
    <QueryClientProvider client={queryClient}>
      <div className="app-root">
        <h1>Study Notes Parser</h1>
        <CourseSelector selectedCourseId={selectedCourseId} onSelect={setSelectedCourseId} />
        {selectedCourseId !== null && (
          <>
            <UploadDropzone courseId={selectedCourseId} />
            <DocumentList courseId={selectedCourseId} />
            <ChatPane courseId={selectedCourseId} onOpenSource={setOpenChunkId} />
          </>
        )}
        {openChunkId !== null && <div data-testid="source-panel-placeholder">chunk {openChunkId}</div>}
      </div>
    </QueryClientProvider>
  );
}
```

- [ ] **Step 7: Write the failing test `frontend/src/components/chat/CitationChip.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CitationChip } from "./CitationChip";

describe("CitationChip", () => {
  it("renders the marker number and calls onOpenSource with the chunk id", () => {
    const onOpenSource = vi.fn();
    render(
      <CitationChip
        citation={{ marker: 1, chunk_id: 42, document_id: 7, filename: "week1.pdf", page_number: 3 }}
        onOpenSource={onOpenSource}
      />
    );
    const chip = screen.getByText("[1]");
    chip.click();
    expect(onOpenSource).toHaveBeenCalledWith(42);
  });
});
```

- [ ] **Step 8: Write the failing test `frontend/src/components/chat/MessageList.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ChatMessage } from "../../api/chat";
import { MessageList } from "./MessageList";

describe("MessageList", () => {
  it("replaces [n] markers with clickable citation chips", () => {
    const onOpenSource = vi.fn();
    const messages: ChatMessage[] = [
      {
        id: 1,
        role: "assistant",
        content: "Mitochondria produce ATP [1].",
        created_at: "2026-01-01T00:00:00Z",
        citations: [{ marker: 1, chunk_id: 5, document_id: 2, filename: "notes.pdf", page_number: 1 }],
      },
    ];

    render(<MessageList messages={messages} onOpenSource={onOpenSource} />);

    expect(screen.getByText("Mitochondria produce ATP", { exact: false })).toBeInTheDocument();
    const chip = screen.getByText("[1]");
    chip.click();
    expect(onOpenSource).toHaveBeenCalledWith(5);
  });

  it("renders plain text markers with no matching citation as-is", () => {
    const messages: ChatMessage[] = [
      {
        id: 1,
        role: "assistant",
        content: "This has an unresolved marker [9].",
        created_at: "2026-01-01T00:00:00Z",
        citations: [],
      },
    ];
    render(<MessageList messages={messages} onOpenSource={() => {}} />);
    expect(screen.getByText("[9]", { exact: false })).toBeInTheDocument();
  });
});
```

- [ ] **Step 9: Run test to verify it fails, then passes**

Run: `docker compose run --rm frontend npm run test`
Expected: FAIL before Steps 1-5 with module-not-found; `6 passed` total after (App, CourseSelector x2, DocumentList, CitationChip, MessageList x2).

- [ ] **Step 10: Commit**

```bash
git add frontend/src/api/chat.ts frontend/src/components/chat/ frontend/src/App.tsx
git commit -m "feat: add chat pane with streaming responses and citation chips"
```

---

## Task 19: Source panel with pdf.js viewer (no highlight — Phase 2 adds that)

**Files:**
- Create: `frontend/src/api/chunks.ts`
- Create: `frontend/src/components/source-panel/SourcePanel.tsx`
- Create: `frontend/src/components/source-panel/PdfViewer.tsx`
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/components/source-panel/SourcePanel.test.tsx`

**Interfaces:**
- Consumes: `apiFetch` (Task 15).
- Produces: `api/chunks.ts` exporting `ChunkDetail` type (`{chunk_id, document_id, filename, pdf_url, page_number, bboxes, text, context_header}`), `useChunkDetail(chunkId: number | null)`. Produces `PdfViewer` component with props `{pdfUrl: string, pageNumber: number}` that loads the PDF via `pdfjs-dist` and renders the given page to a `<canvas>`. Produces `SourcePanel` component with props `{chunkId: number | null, onClose: () => void}`.

- [ ] **Step 1: Write `frontend/src/api/chunks.ts`**

```typescript
import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "./client";

export interface BoundingBox {
  page_width: number;
  page_height: number;
  rects: { x0: number; y0: number; x1: number; y1: number }[];
}

export interface ChunkDetail {
  chunk_id: number;
  document_id: number;
  filename: string;
  pdf_url: string;
  page_number: number;
  bboxes: BoundingBox;
  text: string;
  context_header: string | null;
}

export function useChunkDetail(chunkId: number | null) {
  return useQuery({
    queryKey: ["chunk", chunkId],
    queryFn: () => apiFetch<ChunkDetail>(`/api/chunks/${chunkId}`),
    enabled: chunkId !== null,
  });
}
```

- [ ] **Step 2: Write `frontend/src/components/source-panel/PdfViewer.tsx`**

```tsx
import { useEffect, useRef } from "react";

import * as pdfjsLib from "pdfjs-dist";

pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.mjs",
  import.meta.url
).toString();

interface PdfViewerProps {
  pdfUrl: string;
  pageNumber: number;
}

export function PdfViewer({ pdfUrl, pageNumber }: PdfViewerProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    let cancelled = false;

    async function render() {
      const loadingTask = pdfjsLib.getDocument(pdfUrl);
      const pdf = await loadingTask.promise;
      if (cancelled) return;

      const page = await pdf.getPage(pageNumber);
      const viewport = page.getViewport({ scale: 1.5 });

      const canvas = canvasRef.current;
      if (!canvas) return;
      canvas.width = viewport.width;
      canvas.height = viewport.height;

      const context = canvas.getContext("2d");
      if (!context) return;

      await page.render({ canvasContext: context, viewport }).promise;
    }

    render();
    return () => {
      cancelled = true;
    };
  }, [pdfUrl, pageNumber]);

  return <canvas ref={canvasRef} data-testid="pdf-canvas" />;
}
```

- [ ] **Step 3: Write `frontend/src/components/source-panel/SourcePanel.tsx`**

```tsx
import { useChunkDetail } from "../../api/chunks";
import { PdfViewer } from "./PdfViewer";

interface SourcePanelProps {
  chunkId: number | null;
  onClose: () => void;
}

export function SourcePanel({ chunkId, onClose }: SourcePanelProps) {
  const { data: chunk, isLoading } = useChunkDetail(chunkId);

  if (chunkId === null) return null;

  return (
    <aside className="source-panel" role="complementary" aria-label="Source">
      <header>
        <span>{isLoading ? "Loading..." : `${chunk?.filename} — page ${chunk?.page_number}`}</span>
        <button onClick={onClose} aria-label="Close source panel">
          ×
        </button>
      </header>
      {chunk && <PdfViewer pdfUrl={chunk.pdf_url} pageNumber={chunk.page_number} />}
    </aside>
  );
}
```

- [ ] **Step 4: Modify `frontend/src/App.tsx`**

```tsx
import { useState } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ChatPane } from "./components/chat/ChatPane";
import { CourseSelector } from "./components/courses/CourseSelector";
import { DocumentList } from "./components/documents/DocumentList";
import { UploadDropzone } from "./components/documents/UploadDropzone";
import { SourcePanel } from "./components/source-panel/SourcePanel";

const queryClient = new QueryClient();

export default function App() {
  const [selectedCourseId, setSelectedCourseId] = useState<number | null>(null);
  const [openChunkId, setOpenChunkId] = useState<number | null>(null);

  return (
    <QueryClientProvider client={queryClient}>
      <div className="app-root">
        <h1>Study Notes Parser</h1>
        <CourseSelector selectedCourseId={selectedCourseId} onSelect={setSelectedCourseId} />
        {selectedCourseId !== null && (
          <>
            <UploadDropzone courseId={selectedCourseId} />
            <DocumentList courseId={selectedCourseId} />
            <ChatPane courseId={selectedCourseId} onOpenSource={setOpenChunkId} />
          </>
        )}
        <SourcePanel chunkId={openChunkId} onClose={() => setOpenChunkId(null)} />
      </div>
    </QueryClientProvider>
  );
}
```

- [ ] **Step 5: Write the failing test `frontend/src/components/source-panel/SourcePanel.test.tsx`**

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SourcePanel } from "./SourcePanel";

vi.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: {},
  getDocument: () => ({
    promise: Promise.resolve({
      getPage: () =>
        Promise.resolve({
          getViewport: () => ({ width: 100, height: 100 }),
          render: () => ({ promise: Promise.resolve() }),
        }),
    }),
  }),
}));

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("SourcePanel", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            chunk_id: 5,
            document_id: 2,
            filename: "notes.pdf",
            pdf_url: "/api/documents/2/pdf",
            page_number: 3,
            bboxes: { page_width: 612, page_height: 792, rects: [] },
            text: "Some text",
            context_header: null,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      )
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders nothing when chunkId is null", () => {
    const { container } = renderWithClient(<SourcePanel chunkId={null} onClose={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("fetches and displays the filename and page number", async () => {
    renderWithClient(<SourcePanel chunkId={5} onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText("notes.pdf — page 3")).toBeInTheDocument());
  });

  it("calls onClose when the close button is clicked", async () => {
    const onClose = vi.fn();
    renderWithClient(<SourcePanel chunkId={5} onClose={onClose} />);
    await waitFor(() => screen.getByLabelText("Close source panel"));
    screen.getByLabelText("Close source panel").click();
    expect(onClose).toHaveBeenCalled();
  });
});
```

- [ ] **Step 6: Run test to verify it fails, then passes**

Run: `docker compose run --rm frontend npm run test`
Expected: FAIL before Steps 1-3 with module-not-found; `9 passed` total after.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/chunks.ts frontend/src/components/source-panel/ frontend/src/App.tsx
git commit -m "feat: add source panel with pdf.js viewer opening the cited page"
```

---

## Task 20: End-to-end manual verification of Phase 1

**Files:** none created — this task exercises the running system.

**Interfaces:** none new.

- [ ] **Step 1: Bring up the full stack**

Run: `docker compose up -d`
Expected: all three services (`db`, `backend`, `frontend`) report healthy/running: `docker compose ps`.

- [ ] **Step 2: Apply migrations if not already applied**

Run: `docker compose exec backend alembic upgrade head`
Expected: `Running upgrade -> 0001, initial schema` or `already at head`.

- [ ] **Step 3: Create a course via the UI**

Open `http://localhost:5173`, use the course selector to create a course (e.g. "Biology 101").
Expected: course appears in the list with `(0)` documents.

- [ ] **Step 4: Upload a real PDF and a real DOCX**

Drag two real study-note files (one `.pdf`, one `.docx`) onto the upload dropzone.
Expected: both appear in the document list with status progressing `pending → converting (docx only) → parsing → embedding → ready` within roughly 1-2 minutes depending on file size and whether models are already cached.

- [ ] **Step 5: Start a chat and ask a question the notes actually answer**

Type a question whose answer is in the uploaded notes.
Expected: the response streams in token-by-token, ends with one or more `[n]` citation chips rendered as clickable buttons (not plain text).

- [ ] **Step 6: Click a citation chip**

Expected: the source panel slides in, shows the correct filename and page number in the header, and renders that PDF page via canvas (no highlight box yet — that's expected, Phase 2 territory).

- [ ] **Step 7: Ask a question the notes do NOT answer**

Expected: the assistant states the notes don't cover it, rather than answering from outside knowledge, and does not fabricate citation markers (verify no `[n]` chips reference nonexistent excerpts — check the network tab or debug endpoint if in doubt).

- [ ] **Step 8: Exercise the debug search endpoint**

Run: `curl "http://localhost:8000/api/courses/{course_id}/search?q=your+test+query"`
Expected: JSON with populated `lexical_rank`, `vector_rank`, `fused_rank`, and `reranked` arrays — confirms hybrid retrieval is actually running end to end, not just returning one leg's results.

- [ ] **Step 9: Run the full backend and frontend test suites once more together**

Run: `docker compose run --rm backend pytest -v` and `docker compose run --rm frontend npm run test`
Expected: all backend and frontend tests from Tasks 1-19 pass.

- [ ] **Step 10: Commit any fixes discovered during manual verification**

If Steps 3-8 surfaced bugs, fix them with accompanying test coverage in the relevant task's test file, then:

```bash
git add -A
git commit -m "fix: address issues found during Phase 1 end-to-end verification"
```

If no issues were found, skip this step — there is nothing to commit.

---

## Plan self-review notes

- **Spec coverage:** all 13 spec sections have a corresponding task — schema (Task 2), ingestion (Tasks 3-6), retrieval (Tasks 9-10), reranking (Task 10), generation/citations (Task 12), provider abstraction (Task 11), backend API (Tasks 7, 8, 13, 14), frontend structure (Tasks 15-19), repo layout/compose (Task 1), Phase 1 scope boundary respected throughout (no highlight overlay, no agentic behaviors — those are separate future plans for Phase 2/3 per the spec).
- **Type consistency checked:** `ScoredChunk` (Task 10) used consistently in Tasks 10, 12; `LLMProvider`/`LLMMessage` (Task 11) used consistently in Tasks 12, 13; `CitationInfo`/citation dict shape from `chat_service.py` (Task 12) matches `CitationOut` schema (Task 13) and frontend `Citation` type (Task 18) field-for-field (`marker, chunk_id, document_id, filename, page_number`); `ChunkDraft` (Task 4) fields match what `pipeline.py` (Task 6) consumes.
- **No placeholders:** every step has complete, runnable code; no "TBD"/"add error handling later" language.
