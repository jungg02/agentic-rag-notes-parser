import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_create_and_list_courses(client):
    response = client.post("/api/courses", json={"name": "Organic Chemistry"})
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Organic Chemistry"
    assert body["document_count"] == 0

    response = client.get("/api/courses")
    assert response.status_code == 200
    names = [c["name"] for c in response.json()]
    assert "Organic Chemistry" in names


def test_create_duplicate_course_name_returns_409(client):
    client.post("/api/courses", json={"name": "Physics"})
    response = client.post("/api/courses", json={"name": "Physics"})
    assert response.status_code == 409


def test_update_and_delete_course(client):
    created = client.post("/api/courses", json={"name": "History"}).json()
    course_id = created["id"]

    response = client.patch(f"/api/courses/{course_id}", json={"name": "World History"})
    assert response.status_code == 200
    assert response.json()["name"] == "World History"

    response = client.delete(f"/api/courses/{course_id}")
    assert response.status_code == 204

    response = client.get("/api/courses")
    assert course_id not in [c["id"] for c in response.json()]
