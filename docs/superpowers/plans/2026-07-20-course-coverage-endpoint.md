# Course Coverage Endpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `GET /api/courses/{course_id}/coverage` endpoint that reports how much of each document's content became searchable chunks, including which pages were dropped.

**Architecture:** A new single-purpose router module `backend/app/routers/coverage.py` with a pure `_document_coverage` helper (the arithmetic, DB-free and unit-tested) and a route that runs two queries — one for the course's documents, one grouped over `chunks` — and assembles a per-document report plus a course rollup. Mounted in `main.py` alongside the existing routers.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 (`select`, `func`), Postgres, pytest, FastAPI `TestClient`.

## Global Constraints

- Responses are plain `dict`s, matching the `debug` and `chunks` routers — no new Pydantic schemas.
- `404` (`HTTPException`) when the course does not exist, matching `documents.py`.
- Only `ready` documents contribute to `summary` totals and have non-null coverage fields; `failed`/in-progress documents report `null` coverage and surface `ingest_status`/`ingest_error`.
- `dropped_pages` is returned in full (no cap), ascending.
- `coverage_pct` is rounded to one decimal; `0.0` when the denominator is `0`.
- Spec: `docs/superpowers/specs/2026-07-20-course-coverage-endpoint-design.md`.

---

### Task 1: Coverage arithmetic helper

**Files:**
- Create: `backend/app/routers/coverage.py`
- Test: `backend/tests/routers/test_coverage.py`

**Interfaces:**
- Consumes: nothing (pure function).
- Produces: `_document_coverage(*, document_id: int, filename: str, ingest_status: str, ingest_error: str | None, page_count: int | None, present_pages: set[int], chunk_count: int, token_sum: int) -> dict`. Returns a dict with keys: `document_id`, `filename`, `ingest_status`, `page_count`, `pages_with_text`, `coverage_pct`, `dropped_pages`, `chunks`, `tokens`, `ingest_error`. For non-`ready` documents, `page_count`/`pages_with_text`/`coverage_pct`/`dropped_pages` are `None`; `chunks`/`tokens` still reflect the passed counts.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/routers/test_coverage.py`:

```python
from app.routers.coverage import _document_coverage


def test_document_coverage_ready_partial():
    result = _document_coverage(
        document_id=7,
        filename="lecture03.pptx",
        ingest_status="ready",
        ingest_error=None,
        page_count=5,
        present_pages={1, 2, 4},
        chunk_count=9,
        token_sum=1800,
    )
    assert result["pages_with_text"] == 3
    assert result["dropped_pages"] == [3, 5]
    assert result["coverage_pct"] == 60.0
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
        chunk_count=6,
        token_sum=1200,
    )
    assert result["dropped_pages"] == []
    assert result["coverage_pct"] == 100.0
    assert result["pages_with_text"] == 3


def test_document_coverage_failed_reports_nulls_and_error():
    result = _document_coverage(
        document_id=9,
        filename="broken.pptx",
        ingest_status="failed",
        ingest_error="Unexpected error: boom",
        page_count=None,
        present_pages=set(),
        chunk_count=0,
        token_sum=0,
    )
    assert result["page_count"] is None
    assert result["pages_with_text"] is None
    assert result["coverage_pct"] is None
    assert result["dropped_pages"] is None
    assert result["ingest_status"] == "failed"
    assert result["ingest_error"] == "Unexpected error: boom"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/routers/test_coverage.py -q` (from `backend/`)
Expected: FAIL with `ModuleNotFoundError: No module named 'app.routers.coverage'` (or `ImportError` for `_document_coverage`).

- [ ] **Step 3: Write the minimal implementation**

Create `backend/app/routers/coverage.py`:

```python
def _document_coverage(
    *,
    document_id: int,
    filename: str,
    ingest_status: str,
    ingest_error: str | None,
    page_count: int | None,
    present_pages: set[int],
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
        "chunks": chunk_count,
        "tokens": token_sum,
        "ingest_error": ingest_error,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/routers/test_coverage.py -q` (from `backend/`)
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/coverage.py backend/tests/routers/test_coverage.py
git commit -m "feat: add coverage arithmetic helper"
```

---

### Task 2: Coverage endpoint and wiring

**Files:**
- Modify: `backend/app/routers/coverage.py` (add router + route)
- Modify: `backend/app/main.py` (mount router)
- Test: `backend/tests/routers/test_coverage.py` (add endpoint test + fixtures)

**Interfaces:**
- Consumes: `_document_coverage(...)` from Task 1.
- Produces: `router` (FastAPI `APIRouter`, prefix `/api/courses`) exposing `GET /api/courses/{course_id}/coverage`, returning `{"course_id": int, "summary": dict, "documents": list[dict]}`.

- [ ] **Step 1: Write the failing endpoint test**

Add to `backend/tests/routers/test_coverage.py` (append; keep the Task 1 imports and tests):

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
    from app import config

    monkeypatch.setattr(config.get_settings(), "data_dir", str(tmp_path))
    app.dependency_overrides[get_db] = lambda: real_db_session
    app.dependency_overrides[get_session_factory] = lambda: sessionmaker(bind=test_engine)
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def course(real_db_session):
    course = Course(name="Coverage Test Course")
    real_db_session.add(course)
    real_db_session.commit()
    yield course
    real_db_session.delete(course)
    real_db_session.commit()


def _upload_and_wait_ready(client, course_id, filename, data):
    resp = client.post(
        f"/api/courses/{course_id}/documents",
        files={"files": (filename, io.BytesIO(data), "application/pdf")},
    )
    assert resp.status_code == 202
    document_id = resp.json()[0]["id"]
    deadline = time.time() + 30
    while time.time() < deadline:
        status = client.get(f"/api/documents/{document_id}").json()["ingest_status"]
        if status in ("ready", "failed"):
            break
        time.sleep(0.5)
    assert status == "ready"
    return document_id


def test_coverage_reports_ready_document(client, course, fixtures_dir):
    pdf_bytes = Path(fixtures_dir, "sample.pdf").read_bytes()
    document_id = _upload_and_wait_ready(client, course.id, "sample.pdf", pdf_bytes)

    resp = client.get(f"/api/courses/{course.id}/coverage")
    assert resp.status_code == 200
    body = resp.json()

    assert body["course_id"] == course.id
    assert body["summary"]["documents"] == 1
    assert body["summary"]["ready"] == 1
    assert body["summary"]["total_pages"] == 2
    assert body["summary"]["pages_with_text"] == 2
    assert body["summary"]["coverage_pct"] == 100.0

    doc = next(d for d in body["documents"] if d["document_id"] == document_id)
    assert doc["ingest_status"] == "ready"
    assert doc["page_count"] == 2
    assert doc["pages_with_text"] == 2
    assert doc["dropped_pages"] == []
    assert doc["coverage_pct"] == 100.0
    assert doc["chunks"] >= 2


def test_coverage_404_for_missing_course(client):
    resp = client.get("/api/courses/999999/coverage")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run the endpoint test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/routers/test_coverage.py::test_coverage_404_for_missing_course -q` (from `backend/`, needs Postgres)
Expected: FAIL with `404 != ...` or a routing error / `assert 404 == 200`-style mismatch, because the route does not exist yet (FastAPI returns `404` for an unknown path, so prefer running the full file — `test_coverage_reports_ready_document` will fail clearly since no coverage route exists and the body assertions cannot be satisfied). If Postgres is unavailable locally, this step runs on the Postgres-enabled device.

- [ ] **Step 3: Add the router and route**

Replace the contents of `backend/app/routers/coverage.py` with (the helper from Task 1 is retained, unchanged, below the imports):

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Chunk, Course, Document

router = APIRouter(prefix="/api/courses", tags=["coverage"])


def _document_coverage(
    *,
    document_id: int,
    filename: str,
    ingest_status: str,
    ingest_error: str | None,
    page_count: int | None,
    present_pages: set[int],
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
        "chunks": chunk_count,
        "tokens": token_sum,
        "ingest_error": ingest_error,
    }


@router.get("/{course_id}/coverage")
def course_coverage(course_id: int, db: Session = Depends(get_db)):
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")

    documents = db.scalars(
        select(Document).where(Document.course_id == course_id).order_by(Document.id)
    ).all()

    doc_ids = [d.id for d in documents]
    agg: dict[int, tuple[set[int], int, int]] = {}
    if doc_ids:
        rows = db.execute(
            select(
                Chunk.document_id,
                func.array_agg(distinct(Chunk.page_number)),
                func.count(Chunk.id),
                func.coalesce(func.sum(Chunk.token_count), 0),
            )
            .where(Chunk.document_id.in_(doc_ids))
            .group_by(Chunk.document_id)
        ).all()
        agg = {row[0]: (set(row[1]), row[2], row[3]) for row in rows}

    doc_reports = []
    for d in documents:
        present_pages, chunk_count, token_sum = agg.get(d.id, (set(), 0, 0))
        doc_reports.append(
            _document_coverage(
                document_id=d.id,
                filename=d.original_filename,
                ingest_status=d.ingest_status,
                ingest_error=d.ingest_error,
                page_count=d.page_count,
                present_pages=present_pages,
                chunk_count=chunk_count,
                token_sum=token_sum,
            )
        )

    ready = [r for r in doc_reports if r["ingest_status"] == "ready"]
    total_pages = sum(r["page_count"] for r in ready)
    pages_with_text = sum(r["pages_with_text"] for r in ready)
    summary = {
        "documents": len(doc_reports),
        "ready": len(ready),
        "failed": sum(1 for r in doc_reports if r["ingest_status"] == "failed"),
        "in_progress": sum(
            1 for r in doc_reports if r["ingest_status"] not in ("ready", "failed")
        ),
        "total_pages": total_pages,
        "pages_with_text": pages_with_text,
        "coverage_pct": round(pages_with_text / total_pages * 100, 1) if total_pages else 0.0,
        "total_chunks": sum(r["chunks"] for r in ready),
        "total_tokens": sum(r["tokens"] for r in ready),
    }

    return {"course_id": course_id, "summary": summary, "documents": doc_reports}
```

- [ ] **Step 4: Mount the router in `main.py`**

In `backend/app/main.py`, update the import and add the include. Change:

```python
from app.routers import chat, chunks, courses, debug, documents
```

to:

```python
from app.routers import chat, chunks, courses, coverage, debug, documents
```

and add after `app.include_router(chunks.router)`:

```python
app.include_router(coverage.router)
```

- [ ] **Step 5: Run the coverage tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/routers/test_coverage.py -q` (from `backend/`, needs Postgres for the two endpoint tests; the three Task 1 unit tests pass without a DB)
Expected: PASS (5 passed) on the Postgres-enabled device.

- [ ] **Step 6: Confirm no import regression in the unit tests (DB-free)**

Run: `./.venv/Scripts/python.exe -m pytest tests/routers/test_coverage.py::test_document_coverage_ready_partial tests/routers/test_coverage.py::test_document_coverage_ready_full tests/routers/test_coverage.py::test_document_coverage_failed_reports_nulls_and_error -q` (from `backend/`)
Expected: PASS (3 passed) — runs anywhere, no Postgres.

- [ ] **Step 7: Commit**

```bash
git add backend/app/routers/coverage.py backend/app/main.py backend/tests/routers/test_coverage.py
git commit -m "feat: add course coverage endpoint"
```

---

## Notes for the implementer

- **Postgres for endpoint tests:** the two endpoint tests use the `real_db_session`/`test_engine`/`client` pattern from `tests/routers/test_documents.py` and need a running Postgres (per `tests/conftest.py`). The dev machine may not have one; run those on the Docker-enabled device with `docker compose exec backend pytest tests/routers/test_coverage.py -q`. The three `_document_coverage` unit tests run anywhere.
- **`array_agg(distinct(...))`** returns a Python `list` from psycopg; the route wraps it in `set(...)`. Empty/absent groups are handled by `agg.get(d.id, (set(), 0, 0))`.
- **Verify end-to-end** after implementing: `curl -s http://localhost:8000/api/courses/2/coverage | python -m json.tool` on the device and confirm the `summary` and a document row look right.
