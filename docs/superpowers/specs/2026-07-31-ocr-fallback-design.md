# OCR Fallback for Scanned/Image-Only Pages — Design

**Date:** 2026-07-31
**Status:** Approved (pending spec review)

## Problem

`extract_pages()` (`backend/app/ingestion/parse.py`) pulls text purely from
the PDF's text layer via PyMuPDF. Two failure modes result when a page has
no text layer (a scanned page, or a slide that's just an embedded image):

1. **Fully-scanned document:** every page has no text.
   `pipeline.py`'s aggregate check
   (`total_chars < MIN_TEXT_CHARS_PER_PAGE * len(pages)`) fires and the
   whole document fails ingestion with `IngestionError("No extractable
   text found (scanned document?)")`.
2. **Mixed document:** most pages have text, but a few scanned/image pages
   don't. The aggregate check passes, ingestion "succeeds," but
   `chunker.py` silently skips those specific pages
   (`if not page.lines: continue`) — they're never retrievable. The
   existing coverage endpoint (`GET /api/courses/{id}/coverage`,
   `backend/app/routers/coverage.py`) already surfaces this as
   `dropped_pages`, but nothing recovers them.

## Goal

Add an OCR fallback so both failure modes are recovered automatically,
using a fully local OCR engine (Tesseract) consistent with the rest of the
pipeline's no-external-API-calls design (embeddings and reranking both run
locally already).

Non-goals: OCR quality tuning/preprocessing (deskew, denoise, contrast
correction), non-English languages, a user-facing "retry with OCR" action
(this is fully automatic — folded into the existing pipeline, not a new
step a user triggers), bold detection for OCR'd text.

## Where it hooks in

After `pages = extract_pages(pdf_path)` in `run_ingestion()`
(`backend/app/ingestion/pipeline.py`), a new step examines each page's
native text length. Any page below `MIN_TEXT_CHARS_PER_PAGE` (the same
constant already gating the existing aggregate check) gets OCR'd, and its
`PageLines.lines` is replaced in place with the OCR result. The existing
aggregate check then runs exactly as it does today — now as a final
safety net that only fires if OCR *itself* also failed to extract usable
text from every page (a genuinely blank page, or an image OCR can't read).

This is folded silently into the existing `"parsing"` ingest_status — no
new status value. Ingestion just takes longer for documents that need OCR.

## New module: `backend/app/ingestion/ocr.py`

One function:

```python
def ocr_page(page: fitz.Page, dpi: int = 300) -> list[ExtractedLine]:
    """Render a PDF page to an image and OCR it, returning results in the
    same PDF-point coordinate space and ExtractedLine shape extract_pages()
    produces — so chunker.py needs no changes to consume OCR'd pages."""
```

Implementation: render the page via `page.get_pixmap(matrix=fitz.Matrix(zoom,
zoom))` where `zoom = dpi / 72`, convert to a `PIL.Image`, run
`pytesseract.image_to_data(image, output_type=Output.DICT)`, group words
into lines by Tesseract's `(block_num, par_num, line_num)` keys, and divide
all pixel-space bboxes by `zoom` to convert back into PDF points (matching
the coordinate space `parse.py` already uses, so future bounding-box
highlight rendering, Roadmap Phase 2, doesn't need to special-case OCR'd
chunks). Words below a confidence threshold of 40 (Tesseract's 0-100 `conf` field)
are dropped before grouping — a permissive starting value that keeps
marginal-but-real text while dropping obvious noise; tuning this is exactly
the "quality tuning" explicitly out of scope for v1.

**Font-size approximation:** `ExtractedLine.font_size` is set from the
OCR'd word bbox height (the closest available proxy — Tesseract doesn't
report real font metadata). `bold` is always `False`. This means
`chunker.py`'s existing `_detect_header` heuristic
(`bold or font_size >= 14`) still gets a chance to catch a large scanned
title, entirely through data OCR already provides — no chunker.py changes
needed.

**New module boundary:** `ocr.py` mirrors `parse.py`'s and `convert.py`'s
existing pattern — a single-responsibility ingestion submodule that
`pipeline.py` orchestrates. `ocr.py` doesn't know about
`MIN_TEXT_CHARS_PER_PAGE` or ingestion status; `pipeline.py` owns that
decision, exactly as it already owns the aggregate-threshold decision.

## OCR provenance in the coverage endpoint

`chunks` gets a new column: `is_ocr: Mapped[bool]` (default `False`,
migration `0002_...`). `chunker.py`'s `_make_chunk` needs one addition:
`PageLines` gains an `is_ocr: bool = False` field (set `True` by the new
pipeline step when a page's lines came from OCR), and `ChunkDraft` copies
it through so `pipeline.py`'s `Chunk(...)` construction can set
`is_ocr=draft.is_ocr`.

`coverage.py`'s existing grouped query
(`select(Chunk.document_id, func.array_agg(distinct(Chunk.page_number)),
...)`) gets a second aggregate column, using SQL's `FILTER` clause on the
aggregate:
`func.array_agg(distinct(Chunk.page_number)).filter(Chunk.is_ocr)`
(renders as `array_agg(DISTINCT page_number) FILTER (WHERE is_ocr)`),
producing a per-document set of OCR'd page numbers alongside the existing
all-pages aggregate in the same query — no extra round trip.
`_document_coverage` gains an `ocr_pages: set[int]` parameter and includes
`"ocr_pages": sorted(ocr_pages)` in the response dict for `ready`
documents (`null` for non-ready, matching the existing `dropped_pages`
pattern). No `summary` field changes — this is per-document detail only,
same tier as `dropped_pages`.

## Dependencies

- `tesseract-ocr` — apt package, added to `backend/Dockerfile` the same
  way `libreoffice` already is.
- `pytesseract`, `Pillow` — added to `backend/pyproject.toml`'s
  `dependencies`.

## Testing

Following `test_convert.py`'s existing pattern: tests exercise the real
`tesseract` binary (no mocking), run in Docker/CI where the dependency is
installed (this dev machine doesn't have LibreOffice either — same
existing local-dev limitation, not new).

1. **New fixture:** `scripts/make_fixtures.py` gains an image-only PDF —
   render known text onto a `PIL.Image` (or capture a `fitz` pixmap of
   text drawn on a page, save as PNG, then build a fresh PDF page with
   only `page.insert_image(...)`, no text layer) — saved as
   `backend/tests/fixtures/scanned.pdf`.
2. **`backend/tests/ingestion/test_ocr.py`** (new): `ocr_page()` against
   `scanned.pdf` recovers the known text, with bboxes inside the page's
   dimensions and `font_size > 0`.
3. **`backend/tests/ingestion/test_pipeline.py`** (extend): a
   `run_ingestion` test using `scanned.pdf` (or a doc mixing `sample.pdf`
   content with one image-only page) ends `ready` instead of `failed`,
   and chunks exist for the previously-empty page with `is_ocr=True`;
   chunks from a native-text page still have `is_ocr=False`.
4. **`backend/tests/routers/test_coverage.py`** (extend): a document with
   one OCR'd page reports it in `ocr_pages`; a document with none reports
   `ocr_pages: []`.
