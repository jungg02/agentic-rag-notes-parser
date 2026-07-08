# Study Notes Parser

A personal, local-first app for studying from your own notes. Upload PDFs, Word
docs, and PowerPoint slides, organized into courses. Ask questions in a chat
interface; the app answers **using only your notes** via hybrid (lexical +
embedding) retrieval, cites which excerpt(s) each claim came from, and lets you
click a citation to open a side panel showing the actual source page.

This is Phase 1 of a planned multi-phase build: a plain hybrid-RAG pipeline
(retrieve → rerank → cite), no agentic behaviors yet (see [Roadmap](#roadmap)).

## What it does

- **Organize by course.** Every uploaded document belongs to exactly one
  course; chat is scoped to one course at a time.
- **Upload PDF / DOCX / PPTX.** Non-PDF files are converted to PDF at ingest
  time (headless LibreOffice), so there's a single rendering path for every
  format.
- **Ask questions, get grounded answers.** The assistant answers strictly from
  the retrieved excerpts. If the notes don't cover a question, it says so
  instead of falling back to outside knowledge.
- **Inline citations.** Every factual claim is tagged `[n]`, rendered as a
  clickable chip.
- **Click a citation → see the source.** A slide-out panel opens the actual
  PDF page the claim came from (rendered with `pdf.js`), without leaving the
  chat.
- **Single-user, no auth.** This is a local tool for one person — no login,
  no multi-tenant concerns.

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12, [FastAPI](https://fastapi.tiangolo.com/) |
| Frontend | React 18 + [Vite](https://vitejs.dev/) + TypeScript, [TanStack Query](https://tanstack.com/query) for server state |
| Database | PostgreSQL + [`pgvector`](https://github.com/pgvector/pgvector) (embeddings) and built-in full-text search (`tsvector`) for lexical search |
| Embeddings | [`sentence-transformers`](https://www.sbert.net/), `BAAI/bge-small-en-v1.5` (384-dim, runs locally — no API calls, no per-query cost) |
| Reranking | Local cross-encoder, `cross-encoder/ms-marco-MiniLM-L-6-v2` |
| Document parsing | [PyMuPDF](https://pymupdf.readthedocs.io/) (`fitz`) for text + bounding boxes; `python-pptx` for slide titles |
| Format conversion | Headless [LibreOffice](https://www.libreoffice.org/) (DOCX/PPTX → PDF) |
| PDF rendering | [`pdfjs-dist`](https://mozilla.github.io/pdf.js/), used directly (not `react-pdf`) for canvas-level control |
| LLM | Provider-agnostic — Anthropic or any OpenAI-compatible API (config-driven) |
| Local runtime | Docker Compose (`db`, `backend`, `frontend`) |

Exact dependency versions live in `backend/pyproject.toml` and
`frontend/package.json`.

## How it works

### 1. Ingestion pipeline

Runs as a background task per uploaded file, writing `documents.ingest_status`
at each step (`pending → converting → parsing → embedding → ready`, or
`failed`) so the UI can poll progress:

1. **Store** the original file, compute its SHA-256 (duplicate uploads within
   a course are rejected).
2. **Convert** (skipped for PDFs) — headless LibreOffice turns DOCX/PPTX into
   PDF, with a per-invocation profile directory and a concurrency-1 guard
   (LibreOffice's shared-profile lock silently breaks parallel conversions).
3. **Parse** — PyMuPDF opens the normalized PDF and extracts per-page lines
   with bounding boxes. This is the *only* content-extraction path for every
   format, so citations always point at the file that actually gets
   rendered later.
4. **Chunk** — ~350 tokens/chunk with ~80-token overlap, never crossing a
   page boundary (keeps each chunk's page number and bounding boxes exact).
   Sparse slides get a carried-forward context header (e.g. "Lecture 4 ›
   Photosynthesis") so short chunks still retrieve meaningfully.
5. **Embed** — batch-encode `context_header + text` with
   `BAAI/bge-small-en-v1.5` and store the vectors.
6. **Finalize** — status flips to `ready`. Any failure is recorded in
   `ingest_error` with the original file retained, so ingestion can be
   retried without re-uploading.

Scanned/image-only PDFs with no extractable text fail ingestion loudly rather
than silently producing empty chunks (OCR is a possible future feature, not
part of Phase 1).

### 2. Hybrid retrieval

A query runs down both legs, scoped to the current course, before being
fused and reranked:

```
query → [lexical top-50, vector top-50] → RRF fusion → top-20 → rerank → top-6 → prompt
```

- **Lexical:** Postgres full-text search (`websearch_to_tsquery` +
  `ts_rank_cd`), tolerant of arbitrary phrasing.
- **Vector:** cosine similarity over `pgvector` HNSW index.
- **Fusion:** the two rank lists are combined with Reciprocal Rank Fusion
  (ranks only — raw BM25 and cosine scores are never averaged together).
- **Rerank:** a local cross-encoder re-scores the top 20 fused candidates as
  `(query, excerpt)` pairs; the top 6 go on to generation.

A debug endpoint (`GET /api/courses/{id}/search?q=...`) exposes the raw
lexical/vector/fused/reranked results for tuning.

### 3. Generation and citations

The top 6 excerpts are numbered and inserted into the system prompt with
their filename and page number. The model is instructed to answer only from
those excerpts and mark every claim with `[n]`. The backend validates
citation markers against the excerpts actually sent (dropping anything
invalid), streams the answer token-by-token over SSE, and sends the resolved
`citations` array as one final event — at which point `[n]` upgrades from
plain text to a clickable chip in the UI.

### 4. LLM providers

An `LLMProvider` protocol abstracts generation behind `generate()` /
`generate_stream()`. Anthropic and OpenAI(-compatible) adapters are both
implemented; the OpenAI adapter's `LLM_BASE_URL` passthrough also covers
OpenAI-compatible gateways and local servers. Provider selection is entirely
config-driven — see [Configuration](#configuration).

## Setup guide

### Option A — Docker Compose (recommended)

Requires Docker and Docker Compose.

```bash
cp .env.example .env
# edit .env: set LLM_PROVIDER, LLM_MODEL, LLM_API_KEY (and LLM_BASE_URL if using
# an OpenAI-compatible endpoint instead of Anthropic/OpenAI directly)

docker compose up -d
docker compose exec backend alembic upgrade head
```

Then open:

- **App:** http://localhost:5173
- **API docs:** http://localhost:8000/docs

### Option B — Running natively (no Docker)

Useful in environments without Docker/nested virtualization support.

**Prerequisites:**

- Python 3.12+
- Node.js 20+
- PostgreSQL with the [`pgvector`](https://github.com/pgvector/pgvector)
  extension available (`CREATE EXTENSION vector;`)
- Headless-capable [LibreOffice](https://www.libreoffice.org/) on `PATH`
  (needed for DOCX/PPTX conversion)

**Database:**

```bash
# create a role + database, then enable pgvector inside it
psql -U postgres -c "CREATE ROLE notes LOGIN PASSWORD 'notes';"
psql -U postgres -c "CREATE DATABASE notes OWNER notes;"
psql -U notes -d notes -c "CREATE EXTENSION vector;"
```

**Backend:**

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate   # .venv/bin/activate on macOS/Linux
pip install -e ".[dev]"

cp ../.env.example .env
# edit .env: set DATABASE_URL to point at your local Postgres, plus the
# LLM_* variables as above

alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**Frontend** (separate terminal):

```bash
cd frontend
npm install
npm run dev
```

Then open the app at http://localhost:5173 (the Vite dev server proxies
`/api` to `http://localhost:8000`).

### Subsequent runs

After the one-time setup above, the install, database-creation, and
`.env` steps are done — day-to-day you just start the services. Your
courses, uploaded files, and chat history persist between runs (in the
Postgres database and `DATA_DIR`), so you pick up where you left off.

**With Docker (Option A):**

```bash
docker compose up -d      # start db + backend + frontend
docker compose down       # stop them again
```

**Natively (Option B)** — two terminals:

```bash
# Terminal 1 — backend
cd backend
.venv/Scripts/activate   # .venv/bin/activate on macOS/Linux
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# Terminal 2 — frontend
cd frontend
npm run dev
```

Then open http://localhost:5173 (API docs at http://localhost:8000/docs).
Stop either server with `Ctrl+C`.

Only re-run `alembic upgrade head` (natively) or
`docker compose exec backend alembic upgrade head` (Docker) after pulling
changes that add new database migrations — not on every start. Re-run
`pip install -e ".[dev]"` or `npm install` only when dependencies change.

### Configuration

All backend config lives in `backend/.env` (see `.env.example`):

| Variable | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | Postgres connection string | `postgresql+psycopg://notes:notes@db/notes` (Docker network) |
| `DATABASE_URL_TEST` | Connection string used by the test suite | `...@db/notes_test` |
| `LLM_PROVIDER` | `anthropic` or `openai` | `anthropic` |
| `LLM_MODEL` | Model name for the chosen provider | `claude-opus-4-8` |
| `LLM_API_KEY` | API key for the chosen provider | — |
| `LLM_BASE_URL` | Override for OpenAI-compatible gateways/local servers | unset (uses the provider's default endpoint) |
| `DATA_DIR` | Where uploaded originals + converted PDFs are stored | `/data/files` (Docker volume path) |

`DATABASE_URL` and `DATA_DIR` default to Docker-shaped paths; running
natively, override both in `.env` to point at a real local Postgres instance
and an absolute filesystem path.

### Running the tests

```bash
# backend
cd backend && pytest -v

# frontend
cd frontend && npm run test
```

## Repo layout

```
backend/
  app/
    main.py  config.py  db.py  models.py
    routers/       courses.py documents.py chat.py chunks.py debug.py
    ingestion/      pipeline.py convert.py parse.py chunker.py embedder.py
    retrieval/      lexical.py vector.py fusion.py rerank.py service.py
    generation/     prompts.py chat_service.py
    providers/      base.py anthropic_provider.py openai_provider.py factory.py
  alembic/          # migrations
  tests/
frontend/
  src/
    api/            # TanStack Query hooks per resource
    components/      courses/ documents/ chat/ source-panel/
    App.tsx
docker-compose.yml  # services: db (pgvector), backend (FastAPI+LibreOffice), frontend (Vite)
docs/
  superpowers/specs/    # design spec
  superpowers/plans/    # implementation plan
```

## Roadmap

- **Phase 1 (this build):** course CRUD, ingestion, hybrid retrieval + RRF +
  reranking, streaming chat with citations, source panel that opens to the
  cited page.
- **Phase 2:** bounding-box highlight overlay on the source panel (the exact
  passage, not just the page), plus a debug page to audit bbox quality.
- **Phase 3:** agentic behaviors on the same retrieval core — query
  rewriting, iterative retrieval, multi-hop decomposition for
  compare/contrast questions.

See `docs/superpowers/specs/2026-07-04-study-notes-parser-design.md` for the
full design spec.
