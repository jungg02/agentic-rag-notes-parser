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
