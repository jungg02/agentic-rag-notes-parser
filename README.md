# Study Notes Parser

<video src="https://github.com/user-attachments/assets/ec8fe744-5214-47cf-bb63-e9b0d99d01c7" controls width = "700"></video>

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
| OCR | [Tesseract](https://github.com/tesseract-ocr/tesseract) via `pytesseract`, local fallback for scanned/image-only pages |
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
   rendered later. Any page whose native text is too sparse is OCR'd (local
   Tesseract) as a fallback, replacing that page's text only if OCR recovers
   more than the native extraction did.
4. **Chunk** — ~350 tokens/chunk with ~80-token overlap, never crossing a
   page boundary (keeps each chunk's page number and bounding boxes exact).
   Sparse slides get a carried-forward context header (e.g. "Lecture 4 ›
   Photosynthesis") so short chunks still retrieve meaningfully.
5. **Embed** — batch-encode `context_header + text` with
   `BAAI/bge-small-en-v1.5` and store the vectors.
6. **Finalize** — status flips to `ready`. Any failure is recorded in
   `ingest_error` with the original file retained, so ingestion can be
   retried without re-upload

Scanned/image-only pages are recovered via local OCR (Tesseract) when their
native text is too sparse to be useful — this also handles documents that are
only partially scanned (some pages native, some not). A document where even
OCR can't recover enough text still fails ingestion loudly rather than
silently producing empty chunks.

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
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) on `PATH`
  (needed for OCR fallback on scanned/image-only pages)

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

The backend test suite creates its `notes_test` database on first run but
does **not** migrate it afterwards — `Base.metadata.create_all()` only adds
tables that don't exist yet, it doesn't `ALTER` existing ones for new
columns. If you pull a change that alters the schema (a new model field, a
new Alembic migration) and start seeing column-not-found errors from the
test suite, drop the stale database and let it get recreated:

```bash
docker compose exec db psql -U notes -c "DROP DATABASE notes_test"
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

## Baseline (Phase 0 of the retrieval upgrade plan)

Full audit — module map, request-flow diagrams, honest weaknesses — lives in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Reproduce the numbers below
with `docker compose run --rm backend python bench/baseline.py --course-id 4
--seed 0`; run the retrieval-quality eval (Recall@k/MRR@k across
lexical/vector/fused/reranked) with `backend/scripts/eval_retrieval.py`.
Pin `--course-id`/`--seed` — without them the benchmark picks whichever
course currently has the most chunks, which isn't reproducible as the corpus
grows.

Measured 2026-08-04 (`backend/bench/results/baseline.json`) against course 4
(401 chunks — real ingested course material, not synthetic fixtures; this is
the actual working set queried, since `search_lexical`/`search_vector` are
course-scoped). The dev DB's global corpus across all courses was 26
documents / 839 chunks at measurement time:

| Stage | mean | p50 | p95 | p99 |
|---|---|---|---|---|
| lexical (Postgres FTS) | 1.4ms | 1.1ms | 2.8ms | 4.3ms |
| embed (query encoding) | 34.5ms | 29.7ms | 72.8ms | 85.9ms |
| vector (pgvector ANN search) | 1.3ms | 1.2ms | 1.9ms | 2.0ms |
| fusion (RRF) | <0.1ms | <0.1ms | 0.1ms | 0.1ms |
| hydrate (candidate fetch) | 3.1ms | 2.6ms | 4.7ms | 10.0ms |
| rerank (cross-encoder) | 530.1ms | 468.0ms | 891.3ms | 1037.2ms |
| **end-to-end** | **576.4ms** | **511.0ms** | **966.7ms** | **1026.7ms** |

**Reranking dominates end-to-end latency by nearly an order of magnitude**
over every other stage combined — it's ~92% of mean end-to-end time,
scoring up to 20 real-length note chunks per query through a cross-encoder
on CPU. Everything upstream of it (lexical, embed, vector, fusion, hydrate)
totals well under 50ms even at p99. This is the single most useful number
in this baseline: any future retrieval-quality work (query rewriting,
memory injection, multimodal fusion — Phases 1, 2, 4 of the plan) adds cost
on top of a pipeline that is already reranker-bound, so reranker
latency/throughput is the first thing worth profiling before adding more
stages in front of it. Known weaknesses (broken test-DB fixture blocking
29/60 backend tests on a fresh clone, a silent embedding-truncation gap
above ~512 tokens, dead code, duplicated retrieval logic in the debug
endpoint) are catalogued in `docs/ARCHITECTURE.md`.

## Phase 1: query understanding and working memory

Every chat turn — not only ones that turn out to need a rewrite — now runs
one LLM call before retrieval (`app/query/understanding.py`) that classifies
intent (factual_lookup/comparison/summarization/follow_up) and decides
whether the message can be understood standalone. Only when it can't
(pronouns, dropped subjects, "the other one") does the call also produce a
rewrite, and only then does retrieval run on that rewrite instead of the
original wording (ADR
[001](docs/adr/001-rewrite-model.md)–[003](docs/adr/003-retrieval-fusion.md)).
Classification is unconditional; only the rewrite *action* is conditional —
the LLM round-trip itself is paid every turn regardless.

Session history compacts automatically past a token budget: the last few
turns stay verbatim, everything older gets folded into a rolling summary via
a second, separate LLM call that fires only on turns where the budget was
just crossed (ADR [004](docs/adr/004-compaction-policy.md)). Every turn's
intent, rewrite, and full retrieved-chunk list (not just what got cited) is
now persisted (`query_turns`, `retrieved_chunks`) for Phase 3's eval harness.
The rewritten query is shown in the chat UI ("Interpreted as: ...") when it
differs from what was typed.

**Deferred, not implemented:** Task 2's query expansion (synonym/acronym
expansion for the lexical arm) — the plan itself marks it optional and says
"measure before keeping," and there's no eval harness yet to measure
against (that's Phase 3). Task 3's intent-based routing (retrieving
comparisons per-entity and merging) — intent is classified and persisted
per turn but nothing currently reads it to change retrieval behavior; the
plan frames this as "where it helps," not a hard requirement, and it's a
meaningfully bigger scope addition than the rest of this phase. Both are
one-line ADR-worthy decisions to pick up explicitly if a later phase wants
them, not silent gaps.

**The stop-condition number** — reproduce with
`backend/bench/phase1_query_understanding_latency.py --course-id 4 --seed 0`
(n=15 for understanding, n=8 for compaction; small enough that p95/p99 are
just the 1-2 slowest samples, not a stable tail estimate — treat mean/p50 as
the trustworthy numbers here):

| | mean | p50 | p95 | p99 |
|---|---|---|---|---|
| query understanding (every turn) | 1363.6ms | 1344.2ms | 1593.0ms | 1630.4ms |
| compaction summarization (only on trigger) | 4477.5ms | 3719.9ms | 8254.8ms | 9377.0ms |

Measured against `mistralai/mistral-nemotron` via NVIDIA NIM — a
non-reasoning model. This confirms the earlier hypothesis from testing
against `openai/gpt-oss-20b` (a reasoning model, ~400 hidden tokens spent
"thinking" before a ~30-word JSON reply): understanding's cost dropped from
~4.9s mean to **1.36s mean, ~2.4x Phase 0's retrieval baseline** instead of
~8x. Compaction summarization is still notably slower (4.5s mean) since
it's a genuinely bigger generation task (condensing several turns), not
reasoning overhead — a turn that both classifies and triggers compaction
still pays roughly both on top of the 576ms retrieval baseline, landing
around **6.4s**, down from the ~11s measured against the reasoning model.

Getting this number took several failed attempts against different models
on the same NVIDIA NIM endpoint (`z-ai/glm-5.2` and `mistralai/mistral-medium-3.5-128b`
both hung for 10-800+ seconds on trivial single-token requests — endpoint-side
variance/congestion, not a code bug, confirmed by watching the same calls
complete fine minutes later) — worth knowing if this endpoint gets reused:
its latency is not reliably representative of the model actually being
run, and is worth spot-checking with a trivial call before trusting a full
benchmark run against it.

Full backend test suite: 84 passed, 1 pre-existing unrelated failure (see
Phase 0 section), 1 skipped. Acceptance criteria verified: a 3-turn
conversation with pronoun references retrieves the correct chunk each turn
(automated test, and confirmed live against the real configured provider on
the DSA2101 course — see commit history); rewrites are logged and
inspectable via `query_turns` and the API; compaction triggers, keeps the
verbatim window exact, and — per the fix above — degrades to a one-turn
context gap rather than permanent loss when the model returns nothing
usable (verified by unit tests with a fake provider; not additionally
exercised against the live provider the way understanding was, since
forcing a real multi-thousand-token conversation was out of scope for this
pass).

## Phase 2: semantic memory

Durable, cross-session facts about the student, stored in a new `memories`
table (course-scoped, same embedding model as chunks — ADR 007) and
retrieved alongside chunks each turn. Extraction runs at end-of-session,
detected opportunistically since this app has no scheduler (ADR 008): a
session counts as "ended" once 30 minutes have passed since its last
message, checked when a new session is created for a course. That check
runs at most one extraction (one LLM call) per request, so the worst-case
added latency on session creation is bounded rather than growing with the
number of stale sessions in the course; an earlier version also hooked
this into listing sessions (a GET hit on every course-page load) but that
meant a page load could block on — or hang against — a synchronous LLM
call with no user action to explain the pause, so that hook was removed
(see ADR 008's "Rejected" section for the reasoning and the observed
NVIDIA NIM hang that motivated it). One conservative LLM call (ADR 006)
reviews the session and emits durable facts — topic/struggle/preference/goal
— each with a confidence score; only those above threshold get persisted.

At query time, memories are retrieved by cosine similarity, filtered to a
relevance threshold, capped at 3, and ranked by `similarity × decay_score`
rather than similarity alone (ADR 005, 009) — a newer or more-accessed
memory can outrank a more-similar-but-stale one. That's also how conflicts
are resolved: contradictory facts are never detected or deleted at write
time, both persist, and decay/ranking sort out which one actually surfaces
(ADR 009) — cheaper and more honest than unreliable embedding-based
contradiction detection. `enforce_memory_cap` deletes the lowest-scoring
memories once a course exceeds 50, using the same decay score. A memory
survives its source session being deleted (`source_session_id` is
`SET NULL`, not cascaded) — that's the entire point of "durable." Memories
render in the system prompt in a separate `<student_context>` block,
explicitly marked non-citable, so the citation system needed zero changes.
An inspection endpoint (`GET/DELETE /api/courses/{id}/memories`,
`DELETE /api/memories/{id}`) lists, searches, and deletes memories for
debugging and demoing.

**Acceptance criteria:**
- Facts persist and retrieve in a *later* session — proven directly: a
  memory extracted from a stale Session 1 shows up in a brand-new Session
  2's system prompt (same course), not just within the session that
  produced it.
- Contradictions handled per the documented policy — proven deterministically:
  a hand-constructed stale-but-higher-similarity memory loses to a
  fresh-but-lower-similarity one at retrieval time, exactly ADR 009's
  mechanism, not just an assertion that the code runs.
- Forgetting/decay works and the store is bounded — `enforce_memory_cap`
  evicts the lowest-scoring memories once over 50 per course, scoped so one
  course's growth can't evict another's, and is actually wired into the
  extraction sweep (not just a standalone unused function).
- Memory retrieval adds < 50ms p95 — reproduce with
  `backend/bench/phase2_memory_retrieval_latency.py`. `retrieve_memories()`
  itself, measured with `bump_access=True` (what `chat_service.py` actually
  calls on every turn — includes the per-result attribute writes and
  `db.commit()` the chat path pays for, not just the read-only search):
  **3.12ms mean / 3.65ms p95** against 20 memories (the realistic ceiling —
  courses cap at 50 regardless of usage length, so this isn't a toy-scale
  shortcut the way an early Phase 0 measurement was). The read-only
  `bump_access=False` variant used only by the inspection endpoint's search
  is faster still (1.78ms mean / 2.52ms p95), included in the same run for
  comparison. **Both pass by a wide margin.** Caveat worth being direct
  about: `chat_service.py` embeds the query a second time specifically for
  memory retrieval, rather than threading `retrieve()`'s internal embedding
  through (a deliberate choice to avoid touching `retrieval/service.py`
  this phase). Using Phase 0's own measured embed cost for this model
  (72.8ms p95), the *total* added latency for a turn — embed + retrieve —
  is closer to **~76ms p95**, over budget if the criterion is read as the
  full user-facing cost rather than the retrieval mechanism alone.
  Threading the embedding through instead of recomputing it would close
  this gap; noted as a follow-up, not done here to keep this phase's
  touched-file surface to what was disclosed upfront.
- ADR written for the context budget decision — [ADR 005](docs/adr/005-context-budget.md).

Full backend test suite: 131 passed, 1 pre-existing unrelated failure (see
Phase 0 section), 1 skipped.

## Phase 3: evaluation harness

Proves whether Phases 1 and 2 actually improved retrieval, rather than
just adding features. A 62-item hand-authored test set against real
DSA2101 course data (an R/data-science course), covering 5 categories:
single-turn factual, multi-turn coreference, comparison, topic switch,
and cross-session memory. Every item's grounding (which document/page
should be retrieved) was determined by reading the actual source chunk
and writing a question from it — not LLM-generated and spot-checked, so
grounding is exact by construction rather than probably-correct.

Full methodology, per-category tables, and the honest weaknesses section
this phase's acceptance criteria require: **[bench/results/ablation.md](backend/bench/results/ablation.md)**.
Headline results:

- **Retrieval mechanism** (lexical / vector / fused / fused+rerank):
  reranked wins clearly — **82.3% recall@6** vs. lexical's 33.9%,
  validating the hybrid+rerank design from the original build.
- **Query rewriting (the plan's headline number):** recall@6 on
  multi-turn coreference queries improved from **66.7% to 75.0%** with
  rewriting on. Topic-switch queries (already standalone by construction)
  showed exact parity, 90.0% either way — rewriting helps where needed and
  does no harm where it isn't.
- **Semantic memory:** measured against a real baseline, not a definitional
  zero — the judge was also asked to score personalization for the
  memory-blind answers, given the same fact only as a grading criterion.
  Result: 2/6 with-memory answers judged personalized vs. 2/4 for the
  memory-blind baseline. At n=8 this doesn't support a directional claim
  either way (see the ablation report's honest-weaknesses section for why)
  — the useful finding here is that the harness can measure this at all,
  not that memory helped in this run.
- **LLM-as-judge calibration:** 90.0% agreement on faithfulness, 95.0% on
  citation correctness, against 20 answers I hand-labeled myself. Both
  disagreements were the same failure pattern — an answer citing a real
  excerpt for a claim the excerpt doesn't actually support (correct
  outside knowledge dressed up as sourced) — including one of the 8
  with-memory answers, so that arm's judge-reported 100% faithful is a
  known overestimate; ablation.md §3 has the correction.

Built as a small pipeline of scripts under `backend/bench/phase3_*.py`,
one command runs the whole thing:

```bash
docker compose run --rm backend python bench/phase3_eval.py
```

Collection of live-LLM results (rewrites, generated answers, judge
verdicts) is cached to a resumable JSONL file
(`bench/results/phase3_llm_cache.jsonl`) and run separately from
computing metrics, specifically because the configured NVIDIA NIM
endpoint was observed taking anywhere from ~1s to 381s for a single call
during this build — collection needed to survive that without hanging or
silently discarding slow-but-legitimate results. `phase3_eval.py
--skip-collect` reruns the (fast, zero-LLM) ablation and report steps
against an existing cache. See [ADR 010](docs/adr/010-phase3-eval-design.md)
for the full design rationale.

Full backend test suite: 153 passed, 1 pre-existing unrelated failure
(see Phase 0 section), 1 skipped.

## Phase 4: multimodal retrieval

Extends the corpus beyond text: extracts embedded figures (charts,
diagrams, illustrations) from ingested PDFs, embeds them with SigLIP
(`google/siglip-base-patch16-224`), and searches them independently of
chunk retrieval so a question can surface a relevant diagram alongside —
never instead of — its cited text answer. Scoped to the plan's 5 core
tasks (figure extraction, image embeddings, cross-modal retrieval,
caption enrichment, serving figures with provenance); the two stretch
tasks (video/keyframes, a trained projection head) were never attempted —
see the honest-limitations note below.

**Design decisions** (4 surfaced and confirmed before implementation):
- **[ADR 011](docs/adr/011-image-embedding-model.md):** SigLIP over CLIP — picked on
  merit (generally better zero-shot accuracy at comparable size), no
  switching cost since nothing in this app used CLIP already.
- **[ADR 012](docs/adr/012-figure-index-design.md):** a separate `figures` table
  with its own SigLIP embedding column, entirely independent of `chunks`
  (bge-small text embeddings) — protects the retrieval quality Phase 3
  already measured from any risk of a shared-embedding-space regression.
  Confirmed, not just argued: re-running `phase3_retrieval_ablation.py`
  after this build's corpus was re-ingested twice still reproduces the
  reranked row's 82.3%/0.698/0.740 exactly.
- **[ADR 013](docs/adr/013-cross-modal-surfacing.md):** figures are searched every
  turn and returned as a separate `related_figures` list, never merged
  into the citation-numbered excerpt block or RRF'd against chunk scores
  — chunk-rerank scores and SigLIP cosine similarities aren't the same
  kind of number. Same additive pattern as Phase 2's memory retrieval.
- **[ADR 014](docs/adr/014-captionless-figures.md):** a figure is retrievable via
  image embedding alone the moment it's extracted; captions (when they
  exist) only add lexical-arm presence, never gate retrievability.

**Figure extraction** (`app/ingestion/figures.py`) reuses the PyMuPDF
page-access primitives `ocr.py` already uses, with two empirically-calibrated
filters: images under 100px on their shorter side are dropped (PDFs
exported from PowerPoint can embed hundreds of tiny decorative mask/
gradient fragments that PyMuPDF reports as ordinary images — this
threshold was calibrated directly against this app's own corpus, see the
module docstring), and images covering ≥90% of the page are dropped too —
found directly in this app's own OCR test fixtures, where a scanned page
embeds the *entire page* as one image, which would otherwise get indexed
as a "figure."

**Caption generation (task 4) was evaluated and deferred, not silently
skipped.** Probed directly against the configured provider: a
chat-completion call with an `image_url` content part returns
`BadRequestError: "... is not a multimodal model"` in ~1s — a clean
capability gap, not the endpoint's usual latency flakiness. Wiring up
real vision captioning would mean extending `LLMProvider`/`LLMMessage` to
carry image content across both provider implementations, for a feature
ADR 014 already scoped as optional and the current model can't serve
anyway. `figures.caption`/`caption_tsv` are real, queryable columns —
just empty this build.

**Acceptance criteria:**
- Text query retrieves relevant figures — **44.4% recall@3** on a
  9-item hand-verified eval set (every item's grounding was determined by
  extracting the real figure from the real corpus and visually inspecting
  it before writing the query — see `scripts/eval/phase4_figure_queries.json`).
  Genuinely modest, not inflated: most misses were near-misses on a
  topically-adjacent figure in the same narrow, single-course-illustration
  corpus (e.g. a "how do joins match keys" query landing on a different,
  also-correct join diagram than the one picked as ground truth) —
  disclosed in full in the ablation output, not smoothed over.
- Cross-modal results fused and ranked sensibly — `retrieve_figures()`
  fuses a dense (SigLIP) arm with a lexical (caption) arm via the same
  `reciprocal_rank_fusion()` chunks already use, scoped *within* the
  figures arm only (ADR 013). Since captions are empty this build, fused
  and dense-only results are identical by construction — reported side by
  side rather than hidden, so a future caption pipeline's actual
  contribution would show up as a diff in this same script's output.
- Evaluated with the Phase 3 harness, not just demoed —
  `bench/phase4_figure_ablation.py` reuses `bench/phase3_metrics.py`'s
  exact recall@k/mrr@k/nDCG@10 functions, zero new metric code.
- Honest note on what is and isn't demonstrated — required below.

**Latency, disclosed the same way Phase 2 disclosed its embed cost:**
`stream_assistant_reply` runs `embed_image_query()` (a SigLIP text-tower
forward pass) plus `retrieve_figures()` on every turn, unconditionally —
same additive-always-search pattern as memory retrieval. Measured
steady-state (warm model, matching how `baseline.py`/`phase2_*`/
`phase3_*` all measure — the first unwarmed run mixed in one-time SigLIP
model load and reported a misleading ~950ms): **215.2ms mean** per query
(embed + dense + lexical fusion), roughly **3x** memory retrieval's own
~76ms p95 total-added-latency figure. Unlike memory retrieval this isn't
budgeted against a stated target in the plan, but it's real added
per-turn cost worth being direct about rather than omitting.

**What this is not:** static figure retrieval is not video understanding
— no keyframe sampling, no temporal aggregation, no video input was
built (Phase 4's own stretch task 6, never attempted). Using a pretrained
SigLIP checkpoint as a frozen encoder is not representation learning —
no projection head or adapter was trained on this app's own data (stretch
task 7, also never attempted); every embedding here is exactly what
`google/siglip-base-patch16-224` produces out of the box. Both were
explicitly scoped as stretch goals in the plan ("do this if there's any
time at all") and neither was started.

Reproduce: `docker compose run --rm backend python bench/phase4_figure_ablation.py`.

Full backend test suite: 184 passed, 1 pre-existing unrelated failure
(see Phase 0 section), 1 skipped.

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
