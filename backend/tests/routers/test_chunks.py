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
