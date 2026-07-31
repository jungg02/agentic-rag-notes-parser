# OCR Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically recover text from scanned/image-only PDF pages via local Tesseract OCR, both for fully-scanned documents (currently fail ingestion outright) and individual scanned pages within an otherwise-normal document (currently silently dropped), with OCR provenance visible per-page on the coverage endpoint.

**Architecture:** A new `backend/app/ingestion/ocr.py` module OCRs a single `fitz.Page` and returns results as the existing `ExtractedLine` dataclass, so `chunker.py` needs no changes to consume OCR'd text. `pipeline.py` gains one step, right after `extract_pages()`: any page below the existing `MIN_TEXT_CHARS_PER_PAGE` threshold gets its `lines` replaced by OCR output. A new `chunks.is_ocr` column (plumbed through `PageLines`/`ChunkDraft`) tracks provenance per chunk, surfaced by the coverage endpoint as a new `ocr_pages` field per document.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, Alembic, PyMuPDF (`fitz`), Tesseract OCR via `pytesseract`, `Pillow`.

## Global Constraints

- OCR engine: local Tesseract only — no cloud/LLM vision calls.
- OCR render DPI: `300`.
- OCR word-confidence threshold: `40` (Tesseract's 0-100 `conf` scale); words below this are dropped.
- OCR'd `ExtractedLine.font_size` is approximated from word bbox height (in PDF points, after dividing by the DPI zoom factor); `bold` is always `False` for OCR'd lines.
- Reuse the existing `MIN_TEXT_CHARS_PER_PAGE` constant in `pipeline.py` as the per-page OCR trigger threshold — do not introduce a second, duplicate threshold constant.
- No new `ingest_status` value — OCR is folded silently into the existing `"parsing"` status.
- Non-goals (do not implement): OCR preprocessing/deskew/denoise, non-English language support, a user-facing "retry with OCR" action, bold detection for OCR'd text.
- **Never modify the shared system/base Python environment** (e.g. `/opt/anaconda3`, global `pip install`) to get dependencies working locally. A prior session did this by accident while chasing a broken dependency chain and it broke the shared environment for other projects. If `pytesseract`/`Pillow`/`tesseract` aren't available locally: use the project's actual Docker backend container (`docker compose build backend`, `docker compose run --rm backend python -m pytest ...`, same pattern already used for `run_ingestion`'s DB-backed tests) or an isolated venv created under the scratchpad/tmp directory. Never `pip install` (with or without `--force-reinstall`) against the machine's default/system Python.

---

### Task 1: OCR module, dependencies, and fixture

**Files:**
- Create: `backend/app/ingestion/ocr.py`
- Create: `backend/tests/ingestion/test_ocr.py`
- Modify: `backend/Dockerfile`
- Modify: `backend/pyproject.toml`
- Modify: `scripts/make_fixtures.py`

**Interfaces:**
- Consumes: `ExtractedLine` (dataclass from `app.ingestion.parse`, fields `text: str`, `bbox: tuple[float,float,float,float]`, `font_size: float`, `bold: bool`).
- Produces: `ocr_page(page: fitz.Page, dpi: int = 300) -> list[ExtractedLine]` from `app.ingestion.ocr`, consumed by Task 4. Produces `backend/tests/fixtures/scanned.pdf`, consumed by Task 4 and Task 5's tests.

- [ ] **Step 1: Add dependencies**

In `backend/pyproject.toml`, add to the `dependencies` list (alphabetical position doesn't matter, match the existing list's style):

```toml
    "pytesseract>=0.3.10",
    "Pillow>=10.0",
```

In `backend/Dockerfile`, add `tesseract-ocr` to the existing `apt-get install` list:

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice --no-install-recommends \
    tesseract-ocr \
    fonts-liberation \
    build-essential \
    && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 2: Add the scanned-page fixture**

Append to `scripts/make_fixtures.py` (after the existing `docx.save(...)` line):

```python
scan_doc = fitz.open()
tmp_doc = fitz.open()
tmp_page = tmp_doc.new_page()
tmp_page.insert_text((72, 100), "Chapter 3: Scanned Notes", fontsize=18)
tmp_page.insert_text((72, 140), "Osmosis moves water across a semipermeable membrane.", fontsize=11)
pix = tmp_page.get_pixmap(matrix=fitz.Matrix(2, 2))

scan_page = scan_doc.new_page(width=tmp_page.rect.width, height=tmp_page.rect.height)
scan_page.insert_image(scan_page.rect, pixmap=pix)
scan_doc.save("backend/tests/fixtures/scanned.pdf")
scan_doc.close()
tmp_doc.close()
```

Run `python scripts/make_fixtures.py` from the repo root to generate `backend/tests/fixtures/scanned.pdf` (this only needs `fitz`, already installed — no Tesseract required to generate the fixture, only to OCR it later).

- [ ] **Step 3: Write the failing test**

Create `backend/tests/ingestion/test_ocr.py`:

```python
import fitz

from app.ingestion.ocr import ocr_page


def test_ocr_page_recovers_text_from_image_only_page(fixtures_dir):
    doc = fitz.open(f"{fixtures_dir}/scanned.pdf")
    try:
        page = doc[0]
        lines = ocr_page(page)
    finally:
        doc.close()

    text = " ".join(line.text for line in lines)
    assert "Osmosis" in text
    assert "membrane" in text.lower()
    for line in lines:
        assert 0 <= line.bbox[0] <= page.rect.width
        assert 0 <= line.bbox[1] <= page.rect.height
        assert line.font_size > 0
        assert line.bold is False
```

- [ ] **Step 4: Run the test to verify it fails**

Per the Global Constraints, do not install dependencies against the system Python. Build and use the project's Docker backend container:

```bash
docker compose build backend
docker compose up -d db
# wait for db healthy, then:
docker compose run --rm backend python -m pytest tests/ingestion/test_ocr.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.ingestion.ocr'` (the module doesn't exist yet).

- [ ] **Step 5: Implement `backend/app/ingestion/ocr.py`**

```python
import fitz
import pytesseract
from PIL import Image
from pytesseract import Output

from app.ingestion.parse import ExtractedLine

OCR_DPI = 300
MIN_OCR_CONFIDENCE = 40


def ocr_page(page: fitz.Page, dpi: int = OCR_DPI) -> list[ExtractedLine]:
    """Render a PDF page to an image and OCR it, returning lines in the same
    PDF-point coordinate space and ExtractedLine shape extract_pages()
    produces, so downstream chunking needs no changes to consume OCR'd text.

    Words are grouped into lines using Tesseract's (block, paragraph, line)
    keys, which preserves natural reading order in image_to_data's output --
    no separate sort is needed.
    """
    zoom = dpi / 72
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    data = pytesseract.image_to_data(image, output_type=Output.DICT)

    line_words: dict[tuple[int, int, int], list[int]] = {}
    for i, text in enumerate(data["text"]):
        if not text.strip():
            continue
        if float(data["conf"][i]) < MIN_OCR_CONFIDENCE:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        line_words.setdefault(key, []).append(i)

    lines: list[ExtractedLine] = []
    for indices in line_words.values():
        words = [data["text"][i].strip() for i in indices]
        text = " ".join(w for w in words if w)
        if not text:
            continue
        x0 = min(data["left"][i] for i in indices) / zoom
        y0 = min(data["top"][i] for i in indices) / zoom
        x1 = max(data["left"][i] + data["width"][i] for i in indices) / zoom
        y1 = max(data["top"][i] + data["height"][i] for i in indices) / zoom
        font_size = max(data["height"][i] for i in indices) / zoom
        lines.append(ExtractedLine(text=text, bbox=(x0, y0, x1, y1), font_size=font_size, bold=False))

    return lines
```

- [ ] **Step 6: Run the test to verify it passes**

```bash
docker compose run --rm backend python -m pytest tests/ingestion/test_ocr.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/ingestion/ocr.py backend/tests/ingestion/test_ocr.py backend/Dockerfile backend/pyproject.toml scripts/make_fixtures.py backend/tests/fixtures/scanned.pdf
git commit -m "$(cat <<'EOF'
feat: add OCR module for scanned/image-only PDF pages

Renders a page to an image and runs Tesseract, returning results as
the same ExtractedLine shape extract_pages() produces so chunking
needs no changes to consume OCR'd text.
EOF
)"
```

---

### Task 2: `chunks.is_ocr` schema column

**Files:**
- Modify: `backend/app/models.py`
- Create: `backend/alembic/versions/0002_add_chunk_is_ocr.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `Chunk.is_ocr: bool` (SQLAlchemy mapped column, default `False`), consumed by Task 4 (setting it on chunk creation) and Task 5 (querying it in the coverage endpoint).

- [ ] **Step 1: Add the column to the model**

In `backend/app/models.py`, add `Boolean` to the existing `from sqlalchemy import (...)` block:

```python
from sqlalchemy import (
    Boolean,
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
```

In the `Chunk` class, add the new column right after `token_count`:

```python
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    is_ocr: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
```

- [ ] **Step 2: Write the migration**

Create `backend/alembic/versions/0002_add_chunk_is_ocr.py`:

```python
"""add is_ocr to chunks

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-31
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chunks", sa.Column("is_ocr", sa.Boolean(), nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("chunks", "is_ocr")
```

- [ ] **Step 3: Verify the migration applies**

Per the Global Constraints, use Docker rather than a local Postgres/venv:

```bash
docker compose up -d db
docker compose run --rm backend alembic upgrade head
```

Expected: no errors. Optionally verify with:

```bash
docker compose exec db psql -U notes -d notes -c "\d chunks" 2>&1 | grep is_ocr
```

Expected output: a line showing `is_ocr | boolean | ... not null`.

- [ ] **Step 4: Commit**

```bash
git add backend/app/models.py backend/alembic/versions/0002_add_chunk_is_ocr.py
git commit -m "$(cat <<'EOF'
feat: add is_ocr column to chunks

Tracks whether a chunk's text came from OCR vs native PDF text
extraction, for the coverage endpoint's provenance reporting (Task 5).
EOF
)"
```

---

### Task 3: Thread `is_ocr` through `PageLines` and `ChunkDraft`

**Files:**
- Modify: `backend/app/ingestion/parse.py`
- Modify: `backend/app/ingestion/chunker.py`
- Test: `backend/tests/ingestion/test_chunker.py` (extend existing file)

**Interfaces:**
- Consumes: nothing from earlier tasks (pure dataclass plumbing; does not require the DB column from Task 2 to exist).
- Produces: `PageLines.is_ocr: bool = False` (from `app.ingestion.parse`) and `ChunkDraft.is_ocr: bool` (from `app.ingestion.chunker`), both consumed by Task 4.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/ingestion/test_chunker.py`:

```python
def test_chunks_carry_is_ocr_flag_from_page():
    ocr_page = PageLines(
        page_number=1,
        width=612.0,
        height=792.0,
        rotation=0,
        lines=[_line("Scanned content sentence.")],
        is_ocr=True,
    )
    native_page = PageLines(
        page_number=2,
        width=612.0,
        height=792.0,
        rotation=0,
        lines=[_line("Native content sentence.")],
    )

    chunks = chunk_pages([ocr_page, native_page])

    ocr_chunks = [c for c in chunks if c.page_number == 1]
    native_chunks = [c for c in chunks if c.page_number == 2]
    assert ocr_chunks and all(c.is_ocr for c in ocr_chunks)
    assert native_chunks and all(not c.is_ocr for c in native_chunks)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
docker compose run --rm backend python -m pytest tests/ingestion/test_chunker.py -k is_ocr -v
```

Expected: FAIL — `TypeError: PageLines.__init__() got an unexpected keyword argument 'is_ocr'`.

- [ ] **Step 3: Add `is_ocr` to `PageLines`**

In `backend/app/ingestion/parse.py`, add the field to the dataclass (must come after all non-default fields since it has a default):

```python
@dataclass
class PageLines:
    page_number: int
    width: float
    height: float
    rotation: int
    lines: list[ExtractedLine]
    is_ocr: bool = False
```

- [ ] **Step 4: Add `is_ocr` to `ChunkDraft` and thread it through `_make_chunk`**

In `backend/app/ingestion/chunker.py`:

```python
@dataclass
class ChunkDraft:
    text: str
    context_header: str | None
    page_number: int
    bboxes: dict
    token_count: int
    is_ocr: bool
```

```python
def _make_chunk(lines: list[ExtractedLine], page: PageLines, header: str | None) -> ChunkDraft:
    text = "\n".join(line.text for line in lines)
    embed_text = f"{header}\n{text}" if header else text
    return ChunkDraft(
        text=text,
        context_header=header,
        page_number=page.page_number,
        bboxes={"page_width": page.width, "page_height": page.height, "rects": _merge_rects(lines)},
        token_count=_token_count(embed_text),
        is_ocr=page.is_ocr,
    )
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
docker compose run --rm backend python -m pytest tests/ingestion/test_chunker.py -v
```

Expected: PASS (4/4 tests — 3 pre-existing plus the new one).

- [ ] **Step 6: Commit**

```bash
git add backend/app/ingestion/parse.py backend/app/ingestion/chunker.py backend/tests/ingestion/test_chunker.py
git commit -m "$(cat <<'EOF'
feat: thread is_ocr flag from PageLines through to ChunkDraft

Pure plumbing -- chunk_pages() already receives PageLines per-page, so
copying its is_ocr flag onto each ChunkDraft it produces needs no
signature changes.
EOF
)"
```

---

### Task 4: Wire OCR fallback into `run_ingestion`

**Files:**
- Modify: `backend/app/ingestion/pipeline.py`
- Test: `backend/tests/ingestion/test_pipeline.py` (extend existing file)

**Interfaces:**
- Consumes: `ocr_page(page: fitz.Page, dpi: int = 300) -> list[ExtractedLine]` (Task 1, `app.ingestion.ocr`), `PageLines.is_ocr: bool` (Task 3, `app.ingestion.parse`), `ChunkDraft.is_ocr: bool` (Task 3, `app.ingestion.chunker`), `Chunk.is_ocr` column (Task 2, `app.models`), `backend/tests/fixtures/scanned.pdf` (Task 1).
- Produces: nothing new consumed by later tasks in this plan (Task 5's DB-backed test uploads a document through the API, exercising this task's code, but doesn't import anything from it directly).

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/ingestion/test_pipeline.py`:

```python
def test_run_ingestion_ocrs_scanned_pdf(real_db_session, test_engine, fixtures_dir, tmp_path):
    course = Course(name="Pipeline Test Course OCR")
    real_db_session.add(course)
    real_db_session.commit()

    try:
        doc_dir = tmp_path / "doc4"
        doc_dir.mkdir()
        original = doc_dir / "original.pdf"
        shutil.copy(Path(fixtures_dir) / "scanned.pdf", original)

        document = Document(
            course_id=course.id,
            original_filename="scanned.pdf",
            original_format="pdf",
            original_path=str(original),
            file_sha256="e" * 64,
        )
        real_db_session.add(document)
        real_db_session.commit()
        document_id = document.id

        session_factory = sessionmaker(bind=test_engine)
        run_ingestion(document_id, session_factory)

        real_db_session.expire_all()
        refreshed = real_db_session.get(Document, document_id)
        assert refreshed.ingest_status == "ready"

        chunks = real_db_session.query(Chunk).filter_by(document_id=document_id).all()
        assert len(chunks) >= 1
        assert all(c.is_ocr for c in chunks)
        assert any("osmosis" in c.text.lower() for c in chunks)
    finally:
        real_db_session.delete(course)
        real_db_session.commit()
```

Also extend the existing `test_run_ingestion_pdf_end_to_end` test (which uses the native-text `sample.pdf` fixture) with one new assertion, added right after the existing `assert any("mitochondria" in c.text.lower() for c in chunks)` line:

```python
        assert all(not c.is_ocr for c in chunks)
```

- [ ] **Step 2: Run the tests to verify the new one fails**

```bash
docker compose up -d db
docker compose run --rm backend alembic upgrade head
docker compose run --rm backend python -m pytest tests/ingestion/test_pipeline.py -k "ocr" -v
```

Expected: FAIL — the scanned document ends `ingest_status == "failed"` (with `ingest_error` about no extractable text), not `"ready"`, because `run_ingestion` doesn't OCR anything yet.

- [ ] **Step 3: Wire OCR into `run_ingestion`**

In `backend/app/ingestion/pipeline.py`, update the imports:

```python
import logging
from pathlib import Path
from typing import Callable

import fitz
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.ingestion.chunker import chunk_pages
from app.ingestion.convert import ConversionError, convert_to_pdf
from app.ingestion.embedder import embed_texts
from app.ingestion.ocr import ocr_page
from app.ingestion.parse import PageLines, extract_pages
from app.models import Chunk, Document
```

Add a new private helper, placed after `_set_status` and before `run_ingestion`:

```python
def _ocr_low_text_pages(pdf_path: Path, pages: list[PageLines]) -> None:
    """Replace any page's lines with OCR output if its native text is too
    sparse to be useful. Mutates `pages` in place."""
    needs_ocr = [p for p in pages if sum(len(line.text) for line in p.lines) < MIN_TEXT_CHARS_PER_PAGE]
    if not needs_ocr:
        return

    fitz_doc = fitz.open(pdf_path)
    try:
        for page_lines in needs_ocr:
            fitz_page = fitz_doc[page_lines.page_number - 1]
            page_lines.lines = ocr_page(fitz_page)
            page_lines.is_ocr = True
    finally:
        fitz_doc.close()
```

In `run_ingestion`, change:

```python
            _set_status(db, document_id, "parsing")
            pages = extract_pages(pdf_path)

            total_chars = sum(len(line.text) for page in pages for line in page.lines)
```

to:

```python
            _set_status(db, document_id, "parsing")
            pages = extract_pages(pdf_path)
            _ocr_low_text_pages(pdf_path, pages)

            total_chars = sum(len(line.text) for page in pages for line in page.lines)
```

And in the `Chunk(...)` construction inside the `for index, (draft, vector) in enumerate(zip(drafts, vectors)):` loop, add `is_ocr=draft.is_ocr`:

```python
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
                        is_ocr=draft.is_ocr,
                    )
                )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
docker compose run --rm backend python -m pytest tests/ingestion/test_pipeline.py -v
```

Expected: PASS (9/9 — the 8 pre-existing tests plus the new OCR one; the mock-based tests from the prior delete-during-ingestion fix are included in that count and must still pass unmodified).

- [ ] **Step 5: Run the full backend test suite to check for regressions**

```bash
docker compose run --rm backend python -m pytest tests/ -v
```

Expected: all tests pass except any pre-existing, unrelated failures already present before this plan (e.g. a test requiring a real, valid `LLM_API_KEY` against the live Anthropic API) — do not treat those as regressions from this change; confirm by checking whether they fail identically on `main` before this branch.

- [ ] **Step 6: Commit**

```bash
git add backend/app/ingestion/pipeline.py backend/tests/ingestion/test_pipeline.py
git commit -m "$(cat <<'EOF'
feat: OCR fallback for low-text pages during ingestion

Any page below MIN_TEXT_CHARS_PER_PAGE (the same threshold already
gating the aggregate no-extractable-text failure) gets OCR'd in place
before that aggregate check runs, recovering both fully-scanned
documents and individual scanned pages within otherwise-normal ones.
EOF
)"
```

---

### Task 5: Surface OCR provenance on the coverage endpoint

**Files:**
- Modify: `backend/app/routers/coverage.py`
- Test: `backend/tests/routers/test_coverage.py` (extend existing file)

**Interfaces:**
- Consumes: `Chunk.is_ocr` column (Task 2), `backend/tests/fixtures/scanned.pdf` (Task 1), the end-to-end OCR behavior wired in Task 4 (for the DB-backed test, which uploads through the real API).
- Produces: nothing new consumed elsewhere — this is the last task in the plan.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/routers/test_coverage.py`, update the three existing pure-unit tests to pass the new required `ocr_pages` keyword and assert on it:

```python
def test_document_coverage_ready_partial():
    result = _document_coverage(
        document_id=7,
        filename="lecture03.pptx",
        ingest_status="ready",
        ingest_error=None,
        page_count=5,
        present_pages={1, 2, 4},
        ocr_pages=set(),
        chunk_count=9,
        token_sum=1800,
    )
    assert result["pages_with_text"] == 3
    assert result["dropped_pages"] == [3, 5]
    assert result["coverage_pct"] == 60.0
    assert result["ocr_pages"] == []
    assert result["chunks"] == 9
    assert result["tokens"] == 1800
    assert result["ingest_error"] is None


def test_document_coverage_ready_full():
    result = _document_coverage(
        document_id=8,
        filename="clean.pdf",
        ingest_status="ready",
        ingest_error=None,
        page_count=3,
        present_pages={1, 2, 3},
        ocr_pages=set(),
        chunk_count=6,
        token_sum=1200,
    )
    assert result["dropped_pages"] == []
    assert result["coverage_pct"] == 100.0
    assert result["pages_with_text"] == 3
    assert result["ocr_pages"] == []


def test_document_coverage_failed_reports_nulls_and_error():
    result = _document_coverage(
        document_id=9,
        filename="broken.pptx",
        ingest_status="failed",
        ingest_error="Unexpected error: boom",
        page_count=None,
        present_pages=set(),
        ocr_pages=set(),
        chunk_count=0,
        token_sum=0,
    )
    assert result["page_count"] is None
    assert result["pages_with_text"] is None
    assert result["coverage_pct"] is None
    assert result["dropped_pages"] is None
    assert result["ocr_pages"] is None
    assert result["ingest_status"] == "failed"
    assert result["ingest_error"] == "Unexpected error: boom"
```

Add one new pure-unit test, right after `test_document_coverage_ready_full`:

```python
def test_document_coverage_reports_ocr_pages():
    result = _document_coverage(
        document_id=10,
        filename="scanned.pdf",
        ingest_status="ready",
        ingest_error=None,
        page_count=3,
        present_pages={1, 2, 3},
        ocr_pages={2},
        chunk_count=3,
        token_sum=600,
    )
    assert result["ocr_pages"] == [2]
```

Add one new DB-backed test, at the end of the file:

```python
def test_coverage_reports_ocr_pages_for_document(client, course, fixtures_dir):
    pdf_bytes = Path(fixtures_dir, "scanned.pdf").read_bytes()
    document_id = _upload_and_wait_ready(client, course.id, "scanned.pdf", pdf_bytes)

    resp = client.get(f"/api/courses/{course.id}/coverage")
    assert resp.status_code == 200
    body = resp.json()

    doc = next(d for d in body["documents"] if d["document_id"] == document_id)
    assert doc["ocr_pages"] == [1]
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
docker compose up -d db
docker compose run --rm backend alembic upgrade head
docker compose run --rm backend python -m pytest tests/routers/test_coverage.py -v
```

Expected: FAIL — `TypeError: _document_coverage() missing 1 required keyword-only argument: 'ocr_pages'` for the three updated pure tests and the new pure test; the new DB-backed test fails with a `KeyError`/assertion mismatch since `ocr_pages` isn't in the response yet.

- [ ] **Step 3: Add `ocr_pages` to `_document_coverage`**

In `backend/app/routers/coverage.py`, update the function signature and both return branches:

```python
def _document_coverage(
    *,
    document_id: int,
    filename: str,
    ingest_status: str,
    ingest_error: str | None,
    page_count: int | None,
    present_pages: set[int],
    ocr_pages: set[int],
    chunk_count: int,
    token_sum: int,
) -> dict:
    if ingest_status == "ready" and page_count is not None:
        present = {p for p in present_pages if 1 <= p <= page_count}
        pages_with_text = len(present)
        dropped_pages = [p for p in range(1, page_count + 1) if p not in present]
        coverage_pct = round(pages_with_text / page_count * 100, 1) if page_count else 0.0
        return {
            "document_id": document_id,
            "filename": filename,
            "ingest_status": ingest_status,
            "page_count": page_count,
            "pages_with_text": pages_with_text,
            "coverage_pct": coverage_pct,
            "dropped_pages": dropped_pages,
            "ocr_pages": sorted(p for p in ocr_pages if 1 <= p <= page_count),
            "chunks": chunk_count,
            "tokens": token_sum,
            "ingest_error": ingest_error,
        }
    return {
        "document_id": document_id,
        "filename": filename,
        "ingest_status": ingest_status,
        "page_count": None,
        "pages_with_text": None,
        "coverage_pct": None,
        "dropped_pages": None,
        "ocr_pages": None,
        "chunks": chunk_count,
        "tokens": token_sum,
        "ingest_error": ingest_error,
    }
```

- [ ] **Step 4: Add the `is_ocr`-filtered aggregate to the route**

In `course_coverage`, update the import line to add `Chunk.is_ocr` usage (no new import needed — `Chunk` is already imported), then change the query and the `agg` dict construction:

```python
    doc_ids = [d.id for d in documents]
    agg: dict[int, tuple[set[int], set[int], int, int]] = {}
    if doc_ids:
        rows = db.execute(
            select(
                Chunk.document_id,
                func.array_agg(distinct(Chunk.page_number)),
                func.array_agg(distinct(Chunk.page_number)).filter(Chunk.is_ocr),
                func.count(Chunk.id),
                func.coalesce(func.sum(Chunk.token_count), 0),
            )
            .where(Chunk.document_id.in_(doc_ids))
            .group_by(Chunk.document_id)
        ).all()
        agg = {
            row[0]: (set(row[1]), set(row[2]) if row[2] else set(), row[3], row[4])
            for row in rows
        }
```

And update the per-document loop:

```python
    doc_reports = []
    for d in documents:
        present_pages, ocr_pages, chunk_count, token_sum = agg.get(d.id, (set(), set(), 0, 0))
        doc_reports.append(
            _document_coverage(
                document_id=d.id,
                filename=d.original_filename,
                ingest_status=d.ingest_status,
                ingest_error=d.ingest_error,
                page_count=d.page_count,
                present_pages=present_pages,
                ocr_pages=ocr_pages,
                chunk_count=chunk_count,
                token_sum=token_sum,
            )
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
docker compose run --rm backend python -m pytest tests/routers/test_coverage.py -v
```

Expected: PASS (7/7 — 4 pre-existing plus the 3 new/updated ones... the exact count depends on how many pre-existed; verify all pass, none fail).

- [ ] **Step 6: Run the full backend test suite to check for regressions**

```bash
docker compose run --rm backend python -m pytest tests/ -v
```

Expected: same result as Task 4's Step 5 — all pass except the one pre-existing, unrelated live-API-key test.

- [ ] **Step 7: Commit**

```bash
git add backend/app/routers/coverage.py backend/tests/routers/test_coverage.py
git commit -m "$(cat <<'EOF'
feat: surface OCR provenance on the coverage endpoint

Adds an ocr_pages field per document, aggregated from chunks.is_ocr
via a FILTER'd array_agg in the same query that already computes
dropped_pages -- no extra round trip.
EOF
)"
```

## Self-Review Notes

- **Spec coverage:** all five spec sections (Where it hooks in, New module, OCR provenance, Dependencies, Testing) map to tasks: hooking-in → Task 4; new module → Task 1; provenance → Tasks 2/3/5; dependencies → Task 1; testing → each task's own test steps plus Task 1's fixture.
- **Placeholder scan:** no TBDs; all steps have complete code or exact commands. Task 5 Step 5's "exact count depends on..." is a verification instruction (run and observe), not an unresolved requirement — left as-is since the precise pre-existing count is discoverable by running the suite, and hardcoding a number here risks being wrong if the file's test count drifts.
- **Type consistency:** `ocr_page(page: fitz.Page, dpi: int = 300) -> list[ExtractedLine]` (Task 1) is the exact signature Task 4 imports and calls. `PageLines.is_ocr: bool = False` (Task 3) is set by Task 4's `_ocr_low_text_pages`. `ChunkDraft.is_ocr: bool` (Task 3) is read by Task 4's `Chunk(...)` construction as `is_ocr=draft.is_ocr`. `Chunk.is_ocr` (Task 2) is the column Task 4 writes and Task 5 queries via `Chunk.is_ocr` in the `.filter()` clause — all four names agree.
