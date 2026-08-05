import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.ingestion.embedder import embed_texts
from app.main import app
from app.models import Course, Memory


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed_memories(db_session, texts: list[str]) -> tuple[Course, list[Memory]]:
    course = Course(name="Memory Inspection Test Course")
    db_session.add(course)
    db_session.flush()

    vectors = embed_texts(texts)
    memories = []
    for text, vector in zip(texts, vectors):
        memory = Memory(
            course_id=course.id, content=text, embedding=vector, memory_type="preference", confidence=0.8
        )
        db_session.add(memory)
        memories.append(memory)
    db_session.commit()
    return course, memories


def test_list_memories_returns_all_for_course(db_session, client):
    course, memories = _seed_memories(
        db_session, ["Prefers worked examples.", "Struggles with recursion."]
    )

    response = client.get(f"/api/courses/{course.id}/memories")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert {m["content"] for m in body} == {"Prefers worked examples.", "Struggles with recursion."}


def test_list_memories_404_for_missing_course(client):
    response = client.get("/api/courses/999999/memories")
    assert response.status_code == 404


def test_list_memories_with_search_returns_relevant_only(db_session, client):
    course, memories = _seed_memories(
        db_session, ["Prefers worked examples over abstract theory.", "Enjoys weightlifting on weekends."]
    )

    response = client.get(f"/api/courses/{course.id}/memories", params={"q": "how do they like explanations?"})

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["content"] == "Prefers worked examples over abstract theory."


def test_list_memories_search_does_not_bump_access_count(db_session, client):
    course, memories = _seed_memories(db_session, ["Prefers worked examples over abstract theory."])

    client.get(f"/api/courses/{course.id}/memories", params={"q": "how do they like explanations?"})

    db_session.refresh(memories[0])
    assert memories[0].access_count == 0
    assert memories[0].last_accessed_at is None


def test_delete_memory_removes_it(db_session, client):
    course, memories = _seed_memories(db_session, ["Prefers worked examples."])

    response = client.delete(f"/api/memories/{memories[0].id}")

    assert response.status_code == 204
    assert client.get(f"/api/courses/{course.id}/memories").json() == []


def test_delete_missing_memory_returns_404(client):
    response = client.delete("/api/memories/999999")
    assert response.status_code == 404
