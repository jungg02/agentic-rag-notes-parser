from app.ingestion.embedder import embed_texts
from app.models import Chunk, Course, Document
from app.retrieval.rerank import rerank
from app.retrieval.service import retrieve


def _seed(db_session):
    course = Course(name="Rerank Test Course")
    db_session.add(course)
    db_session.flush()
    document = Document(
        course_id=course.id, original_filename="d.pdf", original_format="pdf",
        original_path="/tmp/d.pdf", file_sha256="e" * 64,
    )
    db_session.add(document)
    db_session.flush()

    texts = [
        "Mitochondria is the powerhouse of the cell and produces ATP through respiration.",
        "The Krebs cycle occurs in the mitochondrial matrix and generates electron carriers.",
        "The Renaissance was a period of cultural rebirth in Europe starting in Italy.",
    ]
    vectors = embed_texts(texts)
    chunks = []
    for i, (t, v) in enumerate(zip(texts, vectors)):
        chunk = Chunk(
            document_id=document.id, course_id=course.id, chunk_index=i, text=t,
            page_number=1, bboxes={"page_width": 612.0, "page_height": 792.0, "rects": []},
            token_count=12, embedding=v,
        )
        db_session.add(chunk)
        chunks.append(chunk)
    db_session.commit()
    return course, chunks


def test_rerank_orders_by_relevance(db_session):
    course, chunks = _seed(db_session)
    scored = rerank("what produces energy in the cell?", chunks, top_k=3)
    assert scored[0].chunk.id in (chunks[0].id, chunks[1].id)
    assert scored[-1].chunk.id == chunks[2].id


def test_retrieve_end_to_end_returns_relevant_chunks_first(db_session):
    course, chunks = _seed(db_session)
    results = retrieve(db_session, course.id, "how does the cell make energy?", top_k=2)
    assert len(results) == 2
    result_ids = {r.chunk.id for r in results}
    assert chunks[2].id not in result_ids
