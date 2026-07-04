# Study Notes Parser — Design Spec

Date: 2026-07-04
Status: Approved for planning

## 1. Summary

A personal, local-first app for studying from your own notes. Upload PDFs, Word docs,
and PowerPoint slides, organized into courses. Ask questions in a chat interface; the
app answers using only your notes via hybrid (BM25 + embedding) retrieval, cites which
excerpt(s) each claim came from, and lets you click a citation to open a side panel
showing the actual source page with the relevant passage highlighted.

## 2. Locked decisions

These were decided during brainstorming and are not open for the implementation plan
to revisit without a new design discussion:

- **Backend:** Python + FastAPI.
- **Frontend:** React + Vite.
- **Database:** local PostgreSQL. `pgvector` extension for embeddings. Plain Postgres
  full-text search (`tsvector` + `ts_rank_cd`) for lexical search — not a dedicated
  BM25 extension, because expected scale is small (~5-15 courses, ~20-50 docs/course,
  ~500-2000 pages total). The lexical-search code is isolated to one module so
  swapping in ParadeDB's `pg_search` later (if scale grows) is a small change.
- **Embeddings:** local model via `sentence-transformers` (`BAAI/bge-small-en-v1.5`,
  384-dim), not an API call — cost, privacy, and offline use.
- **Note organization:** strict courses. A `courses` table; every document belongs to
  exactly one course (`documents.course_id` required FK). Chat is scoped to one course
  at a time.
- **Single-user, no auth.** Runs locally for one person. No users table, no login, no
  session/auth middleware.
- **AI provider:** provider-agnostic from day one via an `LLMProvider` interface.
  Anthropic and OpenAI adapters both implemented; selection is config-driven
  (`LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, optional `LLM_BASE_URL` for
  OpenAI-compatible local servers).
- **Highlighting/rendering:** normalize everything to PDF. DOCX/PPTX are converted to
  PDF at ingest time (headless LibreOffice); the frontend uses one viewer (`pdf.js`)
  and one highlight mechanism (bounding-box overlay) for every source format.
- **Local runtime:** Docker Compose — `db` (postgres+pgvector), `backend` (FastAPI),
  `frontend` (Vite dev server). One command: `docker compose up`.
- **Build philosophy:** Phase 1 is a plain hybrid-RAG pipeline (retrieve → rerank →
  cite) with no agentic behavior. Agentic behaviors (query rewriting, iterative
  retrieval, multi-hop) are an explicit later phase, added once Phase 1's real
  limitations are known — not designed for speculatively now.
- **Fusion:** BM25(tsvector)-leg and embedding-leg results are combined with
  Reciprocal Rank Fusion (RRF), never by averaging raw scores.
- **Reranking:** a local cross-encoder sits between fused retrieval and generation,
  even in Phase 1.
- **Citation UX:** clicking a citation chip never navigates away — it opens a
  slide-out side panel (Claude-web / Google AI Overview style) showing the source PDF
  at the cited page. The highlight box over the exact passage is Phase 2; Phase 1
  opens the panel at the right page without the highlight.

## 3. Data model

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE courses (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE documents (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    course_id         BIGINT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    original_filename TEXT NOT NULL,
    original_format   TEXT NOT NULL CHECK (original_format IN ('pdf','docx','pptx')),
    original_path     TEXT NOT NULL,
    pdf_path          TEXT,                    -- generated PDF; == original_path if original_format='pdf'
    page_count        INTEGER,
    ingest_status     TEXT NOT NULL DEFAULT 'pending'
                      CHECK (ingest_status IN ('pending','converting','parsing',
                                               'embedding','ready','failed')),
    ingest_error      TEXT,
    file_sha256       TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (course_id, file_sha256)
);

CREATE TABLE chunks (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id    BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    course_id      BIGINT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,  -- denormalized for fast filtering
    chunk_index    INTEGER NOT NULL,
    text           TEXT NOT NULL,
    context_header TEXT,                       -- carried-forward heading/slide title
    page_number    INTEGER NOT NULL,            -- 1-based, in the normalized PDF
    bboxes         JSONB NOT NULL,              -- { page_width, page_height, rects: [{x0,y0,x1,y1}, ...] }
    token_count    INTEGER NOT NULL,
    embedding      vector(384) NOT NULL,
    tsv            tsvector GENERATED ALWAYS AS
                     (to_tsvector('english', coalesce(context_header,'') || ' ' || text)) STORED,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, chunk_index)
);

CREATE INDEX chunks_tsv_gin        ON chunks USING GIN (tsv);
CREATE INDEX chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunks_course_idx     ON chunks (course_id);
CREATE INDEX chunks_document_idx   ON chunks (document_id);

CREATE TABLE chat_sessions (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    course_id   BIGINT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    title       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE chat_messages (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id  BIGINT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK (role IN ('user','assistant')),
    content     TEXT NOT NULL,                 -- assistant content keeps [n] markers inline
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE message_citations (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    message_id    BIGINT NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
    chunk_id      BIGINT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    marker_index  INTEGER NOT NULL,
    UNIQUE (message_id, marker_index)
);
```

A small `settings` table (or single-row config table) stores the embedding model name
used to build the current vectors; retrieval refuses to run if it doesn't match the
configured model, rather than silently returning garbage from a dimension/model
mismatch.

Deleting a document cascades to its chunks and any citations pointing at them —
citation links breaking after a deliberate delete is acceptable for a single-user tool
and simpler than snapshotting.

## 4. Ingestion pipeline

Runs as a FastAPI `BackgroundTask` per uploaded file (no Celery/RQ — unnecessary at
this scale). Status is written to `documents.ingest_status` at each step so the upload
UI can poll.

1. **Store original** at `/data/files/{doc_id}/original.{ext}`, compute sha256,
   create the `documents` row (`pending`). Reject duplicate sha256 within a course.
2. **Convert** (`converting`) — skip for PDFs. Otherwise run headless LibreOffice:
   `soffice --headless --norestore --convert-to pdf --outdir ... original.docx`,
   with a per-invocation `UserInstallation` profile dir and a global concurrency-1
   guard (LibreOffice's shared-profile lock silently breaks parallel conversions),
   plus a timeout with retry-on-failure.
3. **Parse** (`parsing`) — open the normalized PDF with PyMuPDF (`fitz`), extract
   per-page lines with bounding boxes (line granularity: fine enough for tight
   highlights, coarse enough to avoid word-level noise). This is the *only* content
   extraction path for every format — DOCX/PPTX are never parsed directly, so bboxes
   are always accurate against the file that gets rendered later. `python-pptx` is
   used only to pull slide titles as metadata (slide index == PDF page index).
4. **Chunk** (still `parsing`) — ~350 tokens/chunk (embedding model's own tokenizer),
   ~80 token overlap, never crossing a page boundary (keeps `page_number` + `bboxes`
   exact and single-page). Sparse slides get a carried-forward `context_header`
   (e.g. "Lecture 4 › Photosynthesis", detected via font-size/position heuristics,
   cross-checked with PPTX slide titles where available) prepended at embedding time
   so short chunks still retrieve meaningfully.
5. **Embed** (`embedding`) — batch-encode `context_header + text` with
   `BAAI/bge-small-en-v1.5` (`normalize_embeddings=True`), insert all chunks in one
   transaction.
6. **Finalize** — `ready` + `page_count` set. Any exception sets `failed` with
   `ingest_error`; original file is retained so ingestion can be retried.

If a document's extracted text is near-empty (scanned/image-only PDF), ingestion
fails loudly with an explicit error rather than silently producing zero useful
chunks. OCR is an explicit future feature, not part of this build.

Embedding and reranking models are loaded once at FastAPI startup as module-level
singletons; the HuggingFace cache is a mounted Docker volume so models download once.

## 5. Hybrid retrieval

Both legs filter by `course_id`.

**Lexical:**
```sql
SELECT id, ts_rank_cd(tsv, q) AS score
FROM chunks, websearch_to_tsquery('english', :query) q
WHERE course_id = :course_id AND tsv @@ q
ORDER BY score DESC LIMIT 50;
```
`websearch_to_tsquery` tolerates arbitrary user phrasing without throwing.

**Vector:**
```sql
SELECT id, 1 - (embedding <=> :query_embedding) AS score
FROM chunks
WHERE course_id = :course_id
ORDER BY embedding <=> :query_embedding LIMIT 50;
```
Cosine distance, HNSW index (chosen over IVFFlat: builds incrementally with no
training step, which matters for a table that starts empty per course). Query
embeddings use the model's query-prefix convention (encoded in one `embed_query()`
helper so it's never misapplied to passage embedding).

**Fusion — Reciprocal Rank Fusion, computed in Python:**
```python
K = 60
def rrf(lexical_ids: list[int], vector_ids: list[int]) -> list[int]:
    scores = defaultdict(float)
    for rank, cid in enumerate(lexical_ids):
        scores[cid] += 1.0 / (K + rank + 1)
    for rank, cid in enumerate(vector_ids):
        scores[cid] += 1.0 / (K + rank + 1)
    return [cid for cid, _ in sorted(scores.items(), key=lambda kv: -kv[1])]
```
Ranks only — raw BM25 and cosine scores are never mixed. Top 20 fused candidates
proceed to reranking.

## 6. Reranking

Local cross-encoder `cross-encoder/ms-marco-MiniLM-L-6-v2` re-scores the 20 fused
candidates as `(query, context_header + text)` pairs; top 6 are kept for generation
(~2k tokens of context). If the top rerank score is very low, generation is told to
hedge ("the notes may not cover this") instead of answering confidently from a weak
match.

Pipeline: `query → [lexical top-50, vector top-50] → RRF → top-20 → rerank → top-6 → prompt`.

## 7. Generation and citations

Retrieved chunks are numbered 1..6 and inserted into the system prompt as labeled
excerpts with filename + page number. The model is instructed to answer only from the
excerpts and mark every factual claim with `[n]`, using multiple markers when a claim
draws on more than one excerpt.

The backend tracks `{marker_number: chunk_id}` for the turn, validates markers found
in the model's output against that map, drops invalid ones, and records one
`message_citations` row per distinct marker used. The chat API returns message text
plus a `citations` array (marker, chunk_id, filename, page); the frontend renders
`[n]` as a clickable chip from that array rather than re-deriving anything client-side.

Streaming: text deltas stream as plain text; the resolved `citations` array is sent as
one final SSE event once generation completes, at which point `[n]` upgrades from
plain text to clickable chips.

## 8. LLM provider abstraction

```python
class LLMMessage: role: Literal["user","assistant"]; content: str
class LLMResponse: text: str; input_tokens: int | None; output_tokens: int | None; stop_reason: str | None

class LLMProvider(Protocol):
    def generate(self, messages, system=None, max_tokens=2048) -> LLMResponse: ...
    def generate_stream(self, messages, system=None, max_tokens=2048) -> Iterator[str]: ...
```

`system` is a first-class parameter (Anthropic takes it top-level, OpenAI as a
message role — each adapter maps it natively). No `tools` parameter yet; that's added
non-breakingly in Phase 3 for agentic tool-use. Config (`LLM_PROVIDER`, `LLM_MODEL`,
`LLM_API_KEY`, optional `LLM_BASE_URL`) drives a factory that returns the configured
provider; call sites depend only on the protocol. `AnthropicProvider` and
`OpenAIProvider` are both implemented; `LLM_BASE_URL` passthrough on the OpenAI
adapter covers OpenAI-compatible local servers for free.

## 9. Backend API surface

```
POST   /api/courses                     {name}
GET    /api/courses
PATCH  /api/courses/{id}
DELETE /api/courses/{id}

POST   /api/courses/{id}/documents      multipart upload (multi-file) -> 202
GET    /api/courses/{id}/documents
GET    /api/documents/{id}              poll target for ingest_status
POST   /api/documents/{id}/retry
DELETE /api/documents/{id}
GET    /api/documents/{id}/pdf          streams normalized PDF (range-request support)

POST   /api/courses/{id}/sessions
GET    /api/courses/{id}/sessions
GET    /api/sessions/{id}/messages
POST   /api/sessions/{id}/messages      {content} -> SSE: delta{text}, done{message_id, citations[]}
DELETE /api/sessions/{id}

GET    /api/chunks/{id}                 {pdf_url, page_number, bboxes, text, context_header, ...}

GET    /api/courses/{id}/search?q=...   debug endpoint: raw lexical/vector/RRF/rerank scores
```

## 10. Frontend structure

TanStack Query for all server state (handles ingest-status polling for free; no
Redux). `pdfjs-dist` used directly (not `react-pdf`) since the highlight overlay
needs raw canvas-level control.

```
App
├─ Sidebar: CourseSelector, SessionList
├─ CourseView
│  ├─ DocumentPanel: UploadDropzone, DocumentList (status chip + retry)
│  └─ ChatPane: MessageList (citation chips), ChatInput
└─ SourcePanel (slide-out): SourceHeader, PdfViewer + HighlightLayer
```

Citation chip click opens `SourcePanel` with the chunk's `chunk_id` — never
navigates away from the chat. `HighlightLayer` (Phase 2) draws bbox rectangles
scaled to the rendered canvas size over the loaded PDF page.

## 11. Repo layout and Docker Compose

```
backend/
  app/
    main.py  config.py  db.py  models.py
    routers/        courses.py documents.py chat.py chunks.py debug.py
    ingestion/       pipeline.py convert.py parse.py chunker.py embedder.py
    retrieval/       lexical.py vector.py fusion.py rerank.py service.py
    generation/      prompts.py chat_service.py
    providers/       base.py anthropic_provider.py openai_provider.py factory.py
  alembic/
frontend/
  src/
    api/
    components/     courses/ documents/ chat/ source-panel/
    App.tsx
docker-compose.yml   # services: db (pgvector), backend (FastAPI+LibreOffice), frontend (Vite)
```

## 12. Phased build order

**Phase 1** — course CRUD; ingestion (upload → convert → parse → chunk → embed →
ready, capturing bboxes even though they aren't rendered yet — free byproduct of
parsing, expensive to retrofit); hybrid retrieval + RRF + reranking (tunable via the
debug search endpoint); chat with streaming + citation chips; source panel that opens
the PDF at the cited page **without** the highlight box.

**Phase 2** — the bbox highlight overlay: scaling math, scroll-to-highlight, resize
handling, plus a debug page that renders every chunk's rects over its page to audit
bbox quality against real documents and fix chunker bugs it surfaces.

**Phase 3** — agentic behaviors layered on the same retrieval core, no schema
changes required: query rewriting first (condense chat history + question into a
standalone search query), then iterative retrieval via a tool-use loop, then
multi-hop decomposition for compare/contrast questions.

## 13. Key risks and mitigations

| Risk | Mitigation |
|---|---|
| Scanned/image PDFs have no text layer | Fail ingestion loudly with a clear error; OCR is a future feature, not silently attempted. |
| PyMuPDF bbox accuracy on rotated/multi-column pages | Line-level rects; store page rotation and skip highlighting (fall back to page-only view) for rotated pages in v1; Phase 2's bbox audit page catches remaining issues. |
| LibreOffice conversion fidelity (complex slides, font substitution) | Cosmetic only — bboxes/text come from the generated PDF, so highlights stay self-consistent even if the PDF doesn't pixel-match the original. Bake common font packages into the image; treat conversion failure as `failed` with retry. |
| LibreOffice concurrency | Per-invocation profile dir + concurrency-1 guard on conversions. |
| Sparse slide chunks retrieve poorly | Carried-forward `context_header` prepended at embed time. |
| Model over/under-cites | Server-side marker validation; invalid markers stripped; a response with zero citations is flagged in the UI. |
| Embedding model swapped later | Model name stored in config/settings table; retrieval refuses to run on mismatch rather than return garbage. |
