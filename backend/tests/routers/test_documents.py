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
