import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.ingestion.image_embedder import embed_image_query
from app.main import app
from app.models import Course, Document, Figure


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed_course_and_document(db_session) -> tuple[Course, Document]:
    course = Course(name="Figure Router Test Course")
    db_session.add(course)
    db_session.flush()
    document = Document(
        course_id=course.id, original_filename="slides.pdf", original_format="pdf",
        original_path="/tmp/slides.pdf", file_sha256="b" * 64,
    )
    db_session.add(document)
    db_session.flush()
    return course, document


def _add_figure(db_session, course, document, page_number, image_path, embedding) -> Figure:
    figure = Figure(
        course_id=course.id, document_id=document.id, page_number=page_number,
        image_path=image_path,
        bbox={"page_width": 612.0, "page_height": 792.0, "x0": 0, "y0": 0, "x1": 100, "y1": 100},
        embedding=embedding,
    )
    db_session.add(figure)
    return figure


def test_list_figures_returns_all_for_course(db_session, client, tmp_path):
    course, document = _seed_course_and_document(db_session)
    vec = embed_image_query("a diagram")
    _add_figure(db_session, course, document, 1, str(tmp_path / "a.png"), vec)
    _add_figure(db_session, course, document, 2, str(tmp_path / "b.png"), vec)
    db_session.commit()

    response = client.get(f"/api/courses/{course.id}/figures")

    assert response.status_code == 200
    assert len(response.json()) == 2


def test_list_figures_404_for_missing_course(client):
    response = client.get("/api/courses/999999/figures")
    assert response.status_code == 404


def test_list_figures_with_search_uses_retrieve_figures(db_session, client, tmp_path):
    course, document = _seed_course_and_document(db_session)
    query = "a diagram of the water cycle"
    matching = _add_figure(db_session, course, document, 1, str(tmp_path / "a.png"), embed_image_query(query))
    db_session.commit()

    response = client.get(f"/api/courses/{course.id}/figures", params={"q": query})

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == matching.id


def test_get_figure_image_returns_file_bytes(db_session, client, tmp_path):
    course, document = _seed_course_and_document(db_session)
    image_path = tmp_path / "real.png"
    image_path.write_bytes(b"fake-png-bytes")
    figure = _add_figure(db_session, course, document, 1, str(image_path), embed_image_query("x"))
    db_session.commit()

    response = client.get(f"/api/figures/{figure.id}/image")

    assert response.status_code == 200
    assert response.content == b"fake-png-bytes"


def test_get_figure_image_404_for_missing_figure(client):
    response = client.get("/api/figures/999999/image")
    assert response.status_code == 404


def test_get_figure_image_404_when_file_missing_on_disk(db_session, client, tmp_path):
    course, document = _seed_course_and_document(db_session)
    figure = _add_figure(
        db_session, course, document, 1, str(tmp_path / "gone.png"), embed_image_query("x")
    )
    db_session.commit()

    response = client.get(f"/api/figures/{figure.id}/image")

    assert response.status_code == 404
