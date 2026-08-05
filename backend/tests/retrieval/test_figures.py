from app.models import Course, Document, Figure
from app.retrieval.figures import FIGURE_SIMILARITY_THRESHOLD, retrieve_figures

DIM = 768


def _unit(index: int) -> list[float]:
    v = [0.0] * DIM
    v[index] = 1.0
    return v


def _blend(e1: list[float], e2: list[float], sim: float) -> list[float]:
    other = (1 - sim**2) ** 0.5
    return [sim * a + other * b for a, b in zip(e1, e2)]


def _seed_course_and_document(db_session, name="Figure Retrieval Test Course") -> tuple[Course, Document]:
    course = Course(name=name)
    db_session.add(course)
    db_session.flush()
    document = Document(
        course_id=course.id, original_filename="slides.pdf", original_format="pdf",
        original_path="/tmp/slides.pdf", file_sha256="a" * 64,
    )
    db_session.add(document)
    db_session.flush()
    return course, document


def _add_figure(db_session, course, document, page_number, embedding, caption=None) -> Figure:
    figure = Figure(
        course_id=course.id, document_id=document.id, page_number=page_number,
        image_path=f"/tmp/p{page_number}.png",
        bbox={"page_width": 612.0, "page_height": 792.0, "x0": 0, "y0": 0, "x1": 100, "y1": 100},
        embedding=embedding, caption=caption,
    )
    db_session.add(figure)
    return figure


def test_retrieves_figure_above_similarity_threshold(db_session):
    course, document = _seed_course_and_document(db_session)
    e1, e2 = _unit(0), _unit(1)
    relevant = _add_figure(db_session, course, document, 1, _blend(e1, e2, FIGURE_SIMILARITY_THRESHOLD + 0.1))
    db_session.commit()

    results = retrieve_figures(db_session, course.id, "a diagram", e1)

    assert len(results) == 1
    assert results[0].figure.id == relevant.id
    assert results[0].similarity > FIGURE_SIMILARITY_THRESHOLD


def test_figure_below_threshold_not_returned(db_session):
    course, document = _seed_course_and_document(db_session)
    e1, e2 = _unit(0), _unit(1)
    _add_figure(db_session, course, document, 1, _blend(e1, e2, FIGURE_SIMILARITY_THRESHOLD - 0.05))
    db_session.commit()

    results = retrieve_figures(db_session, course.id, "a diagram", e1)

    assert results == []


def test_retrieval_scoped_to_course(db_session):
    course, document = _seed_course_and_document(db_session, name="Figure Retrieval Test Course A")
    other_course, other_document = _seed_course_and_document(db_session, name="Figure Retrieval Test Course B")
    e1, e2 = _unit(0), _unit(1)
    _add_figure(db_session, other_course, other_document, 1, _blend(e1, e2, 0.5))
    db_session.commit()

    results = retrieve_figures(db_session, course.id, "a diagram", e1)

    assert results == []


def test_capped_at_limit(db_session):
    course, document = _seed_course_and_document(db_session)
    e1, e2 = _unit(0), _unit(1)
    for page in range(1, 6):
        _add_figure(db_session, course, document, page, _blend(e1, e2, FIGURE_SIMILARITY_THRESHOLD + 0.1))
    db_session.commit()

    results = retrieve_figures(db_session, course.id, "a diagram", e1, limit=2)

    assert len(results) == 2


def test_captionless_figure_still_retrievable_via_dense_arm(db_session):
    """ADR 014: a null caption must never block dense retrieval."""
    course, document = _seed_course_and_document(db_session)
    e1, e2 = _unit(0), _unit(1)
    figure = _add_figure(db_session, course, document, 1, _blend(e1, e2, FIGURE_SIMILARITY_THRESHOLD + 0.1), caption=None)
    db_session.commit()

    results = retrieve_figures(db_session, course.id, "completely unrelated caption text", e1)

    assert len(results) == 1
    assert results[0].figure.id == figure.id


def test_lexical_arm_surfaces_a_figure_the_dense_arm_missed(db_session):
    """A figure with a strong caption match but a weak (sub-threshold)
    embedding similarity should still surface via the lexical arm --
    exactly the same graceful-degradation-in-the-other-direction as
    chunks' hybrid search."""
    course, document = _seed_course_and_document(db_session)
    e1, e2 = _unit(0), _unit(1)
    figure = _add_figure(
        db_session, course, document, 1,
        _blend(e1, e2, FIGURE_SIMILARITY_THRESHOLD - 0.05),
        caption="A scatter plot of penguin bill length versus body mass",
    )
    db_session.commit()

    results = retrieve_figures(db_session, course.id, "scatter plot penguin bill length", e1)

    assert len(results) == 1
    assert results[0].figure.id == figure.id
    # below-threshold dense candidates are dropped before fusion, so a
    # lexical-only hit's similarity is simply absent, not just low.
    assert results[0].similarity is None
