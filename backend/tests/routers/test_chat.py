import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.ingestion.embedder import embed_texts
from app.main import app
from app.models import Chunk, Course, Document
from app.providers.factory import get_provider


class FakeProvider:
    def generate(self, messages, system=None, max_tokens=2048):
        raise NotImplementedError

    def generate_stream(self, messages, system=None, max_tokens=2048):
        yield "Mitochondria produce ATP [1]. "


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_provider] = lambda: FakeProvider()
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def course_with_chunk(db_session):
    course = Course(name="Chat Test Course")
    db_session.add(course)
    db_session.flush()
    document = Document(
        course_id=course.id, original_filename="notes.pdf", original_format="pdf",
        original_path="/tmp/notes.pdf", file_sha256="9" * 64,
    )
    db_session.add(document)
    db_session.flush()
    vectors = embed_texts(["Mitochondria produce ATP through cellular respiration."])
    chunk = Chunk(
        document_id=document.id, course_id=course.id, chunk_index=0,
        text="Mitochondria produce ATP through cellular respiration.",
        page_number=1, bboxes={"page_width": 612.0, "page_height": 792.0, "rects": []},
        token_count=10, embedding=vectors[0],
    )
    db_session.add(chunk)
    db_session.commit()
    return course


def test_create_session_and_send_message(client, course_with_chunk):
    session_resp = client.post(f"/api/courses/{course_with_chunk.id}/sessions")
    assert session_resp.status_code == 201
    session_id = session_resp.json()["id"]

    message_resp = client.post(f"/api/sessions/{session_id}/messages", json={"content": "What produces ATP?"})
    assert message_resp.status_code == 200
    assert "event: delta" in message_resp.text
    assert "event: done" in message_resp.text

    messages_resp = client.get(f"/api/sessions/{session_id}/messages")
    messages = messages_resp.json()
    assert len(messages) == 2  # user + assistant
    assistant_message = messages[1]
    assert assistant_message["role"] == "assistant"
    assert len(assistant_message["citations"]) == 1
    assert assistant_message["citations"][0]["page_number"] == 1


def test_delete_session(client, course_with_chunk):
    session_id = client.post(f"/api/courses/{course_with_chunk.id}/sessions").json()["id"]
    response = client.delete(f"/api/sessions/{session_id}")
    assert response.status_code == 204
