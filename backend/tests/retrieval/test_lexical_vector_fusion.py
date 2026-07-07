from app.ingestion.embedder import embed_query, embed_texts
from app.models import Chunk, Course, Document
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.lexical import search_lexical
from app.retrieval.vector import search_vector


def _seed_course_with_chunks(db_session, name="Retrieval Test Course"):
    course = Course(name=name)
    db_session.add(course)
    db_session.flush()

    document = Document(
        course_id=course.id,
        original_filename="doc.pdf",
        original_format="pdf",
        original_path="/tmp/doc.pdf",
        file_sha256="d" * 64,
    )
    db_session.add(document)
    db_session.flush()

    texts = [
        "Mitochondria is the powerhouse of the cell and produces ATP.",
        "Photosynthesis converts sunlight into chemical energy in plants.",
        "The French Revolution began in 1789 and reshaped European politics.",
    ]
    vectors = embed_texts(texts)

    chunks = []
    for i, (t, v) in enumerate(zip(texts, vectors)):
        chunk = Chunk(
            document_id=document.id,
            course_id=course.id,
            chunk_index=i,
            text=t,
            page_number=1,
            bboxes={"page_width": 612.0, "page_height": 792.0, "rects": []},
            token_count=10,
            embedding=v,
        )
        db_session.add(chunk)
        chunks.append(chunk)
    db_session.commit()
    return course, chunks


def test_lexical_search_finds_keyword_match(db_session):
    course, chunks = _seed_course_with_chunks(db_session)
    results = search_lexical(db_session, course.id, "mitochondria ATP")
    assert results
    assert results[0] == chunks[0].id


def test_lexical_search_scoped_to_course(db_session):
    course, chunks = _seed_course_with_chunks(db_session)
    other_course, _ = _seed_course_with_chunks(db_session, name="Other Retrieval Test Course")
    results = search_lexical(db_session, other_course.id, "mitochondria")
    assert chunks[0].id not in results


def test_vector_search_finds_semantic_match(db_session):
    course, chunks = _seed_course_with_chunks(db_session)
    query_embedding = embed_query("what generates energy in a cell?")
    results = search_vector(db_session, course.id, query_embedding, limit=3)
    # The mitochondria and photosynthesis chunks are both about biological
    # energy production and score very closely for this query (measured
    # directly: 0.7478 vs 0.7488 cosine similarity) — asserting an exact
    # winner between them is flaky. What matters for this test is that a
    # semantically related chunk beats the unrelated French Revolution one.
    assert results[0] in (chunks[0].id, chunks[1].id)
    assert results[-1] == chunks[2].id


def test_reciprocal_rank_fusion_combines_rankings():
    lexical = [1, 2, 3]
    vector = [2, 1, 4]
    fused = reciprocal_rank_fusion([lexical, vector])
    # chunk 1 and 2 both appear near the top of both lists, so should outrank 3/4
    assert fused[0] in (1, 2)
    assert fused[1] in (1, 2)
    assert set(fused) == {1, 2, 3, 4}
