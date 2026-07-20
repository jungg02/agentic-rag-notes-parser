# Course Coverage Endpoint — Design

**Date:** 2026-07-20
**Status:** Approved (pending spec review)

## Problem

A document's `ingest_status` of `ready` does **not** mean all of its content was
parsed into searchable chunks. `extract_pages` yields one page-record per slide
and `pipeline.py` sets `page_count` to that total, but the chunker skips any page
that produced no text lines (`chunker.py:60`, `if not page.lines: continue`).
So an image- or diagram-heavy slide deck can be `ready` while silently dropping
pages that never became chunks — and therefore can never be retrieved.

Users currently have no way to see how much of their uploaded content actually
made it into the index. This feature exposes that at the page level.

## Goal

Provide a read-only endpoint that reports, per course, how much of each
document's content was successfully parsed into chunks, including exactly which
pages were dropped.

Non-goals: retrieval/answer accuracy evaluation (a separate effort), any UI, and
any change to ingestion behavior.

## Definitions

- **pages_with_text** — the count of distinct `page_number` values present in the
  `chunks` table for a document.
- **dropped_pages** — the page numbers in `1..page_count` that have **no** chunk
  row. These are the pages that produced no searchable text.
- **coverage_pct** — `pages_with_text / page_count * 100`, rounded to one decimal.

Only `ready` documents have a non-null `page_count` (it is set at
`pipeline.py:79` on success). Documents that are `failed` or still in progress
(`pending`/`converting`/`parsing`/`embedding`) have `page_count = NULL` and no
chunks; for these, all coverage fields are `null` and the response surfaces
`ingest_status` and `ingest_error` instead.

## API

```
GET /api/courses/{course_id}/coverage
```

- `404` if the course does not exist (matches `documents.py`).
- `200` with the JSON body below otherwise.

### Response shape

```json
{
  "course_id": 2,
  "summary": {
    "documents": 13,
    "ready": 11,
    "failed": 1,
    "in_progress": 1,
    "total_pages": 440,
    "pages_with_text": 388,
    "coverage_pct": 88.2,
    "total_chunks": 1203,
    "total_tokens": 210450
  },
  "documents": [
    {
      "document_id": 7,
      "filename": "lecture03.pptx",
      "ingest_status": "ready",
      "page_count": 40,
      "pages_with_text": 31,
      "coverage_pct": 77.5,
      "dropped_pages": [4, 9, 15, 22, 28, 33, 34, 39, 40],
      "chunks": 96,
      "tokens": 18320,
      "ingest_error": null
    },
    {
      "document_id": 9,
      "filename": "lecture05.pptx",
      "ingest_status": "failed",
      "page_count": null,
      "pages_with_text": null,
      "coverage_pct": null,
      "dropped_pages": null,
      "chunks": 0,
      "tokens": 0,
      "ingest_error": "Unexpected error: ..."
    }
  ]
}
```

### Field rules

- `documents` is ordered by `document_id` ascending.
- `dropped_pages` returns **all** dropped page numbers (no cap), ascending. For a
  `ready` document with full coverage it is `[]`.
- `summary` counts:
  - `documents` — all documents in the course.
  - `ready` / `failed` — counts by status. `in_progress` — everything else
    (`pending`, `converting`, `parsing`, `embedding`).
  - `total_pages`, `pages_with_text`, `total_chunks`, `total_tokens` — summed over
    **ready documents only**, so `coverage_pct` is not diluted by files that have
    not finished processing. `total_chunks`/`total_tokens` still reflect only what
    is indexed, which is what a coverage number should measure.
  - `coverage_pct` — `pages_with_text / total_pages * 100`, rounded to one
    decimal; `0.0` when `total_pages` is `0` (no ready documents).

## Components

New module `backend/app/routers/coverage.py`, mounted in `main.py` alongside the
other routers.

### Pure helper (unit-testable, no DB)

```python
def _document_coverage(
    *, document_id, filename, ingest_status, ingest_error,
    page_count, present_pages, chunk_count, token_sum,
) -> dict
```

- `present_pages` is the set of distinct page numbers found in chunks.
- For `ready` documents: computes `pages_with_text`, `coverage_pct`, and
  `dropped_pages` (complement of `present_pages` within `1..page_count`).
- For non-`ready` documents: coverage fields are `null`; `chunks`/`tokens` still
  reported (normally 0).

Keeping this arithmetic in a pure function means it can be tested without a
database, on any machine.

### Data access

Two queries, no N+1:

1. Documents for the course: `id, original_filename, ingest_status, ingest_error,
   page_count`.
2. Grouped over `chunks` filtered to those document ids:
   `document_id, array_agg(DISTINCT page_number), count(*), coalesce(sum(token_count), 0)`
   grouped by `document_id`.

The route joins the two results in Python, calls `_document_coverage` per
document, and assembles the `summary`.

Responses are plain `dict`s, consistent with the `debug` and `chunks` routers.
No new Pydantic schemas.

## Error handling

- Missing course → `404` (`HTTPException`), same pattern as `get_document`.
- A document with `page_count` set but `present_pages` containing a page number
  greater than `page_count` (should not happen) → the helper ignores out-of-range
  pages when computing the complement; `pages_with_text` still counts distinct
  present pages. This is defensive only.

## Testing

Test-driven, following the repo's existing patterns.

1. **Unit tests for `_document_coverage`** (`tests/routers/test_coverage.py`), no DB:
   - ready doc, partial coverage → correct `pages_with_text`, `dropped_pages`,
     `coverage_pct`.
   - ready doc, full coverage → `dropped_pages == []`, `coverage_pct == 100.0`.
   - failed doc → coverage fields `null`, `ingest_error` surfaced.
2. **DB-backed endpoint test** (`tests/routers/test_coverage.py`, uses the
   `client` + real ingestion like `test_documents.py`/`test_pipeline.py`):
   ingest `sample.pdf` into a course, `GET /api/courses/{id}/coverage`, assert the
   `summary` and the document row. Runs against Postgres (not on the dev machine
   that lacks a DB).

The unit tests give local, DB-free confidence in the arithmetic; the endpoint
test verifies wiring and SQL against real Postgres.

## Out of scope / future

- Frontend surfacing of coverage.
- Flagging low-coverage documents or thresholds/alerts.
- Retrieval and answer-accuracy evaluation (separate feature).
