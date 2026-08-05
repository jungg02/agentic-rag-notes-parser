from datetime import datetime, timedelta, timezone

from app.ingestion.embedder import embed_query, embed_texts
from app.memory.retrieval import retrieve_memories
from app.models import Course, Memory


def _seed_course_with_memories(db_session, texts: list[str], name="Memory Retrieval Test Course") -> tuple[Course, list[Memory]]:
    course = Course(name=name)
    db_session.add(course)
    db_session.flush()

    vectors = embed_texts(texts)
    memories = []
    for text, vector in zip(texts, vectors):
        memory = Memory(
            course_id=course.id,
            content=text,
            embedding=vector,
            memory_type="preference",
            confidence=0.8,
        )
        db_session.add(memory)
        memories.append(memory)
    db_session.commit()
    return course, memories


def test_retrieves_relevant_memory_above_threshold(db_session):
    course, memories = _seed_course_with_memories(
        db_session,
        ["Prefers worked examples over abstract theory.", "Enjoys weightlifting on weekends."],
    )
    query_embedding = embed_query("Can you show me a worked example instead of the theory?")

    results = retrieve_memories(db_session, course.id, query_embedding)

    assert len(results) >= 1
    assert results[0].memory.id == memories[0].id


def test_no_memories_returned_when_none_relevant(db_session):
    course, memories = _seed_course_with_memories(
        db_session, ["Enjoys weightlifting on weekends.", "Owns a golden retriever named Max."]
    )
    query_embedding = embed_query("What is the time complexity of merge sort?")

    results = retrieve_memories(db_session, course.id, query_embedding)

    assert results == []
    db_session.refresh(memories[0])
    assert memories[0].access_count == 0  # nothing retrieved, nothing touched


def test_retrieval_scoped_to_course(db_session):
    course, memories = _seed_course_with_memories(
        db_session, ["Prefers worked examples over abstract theory."], name="Course A"
    )
    other_course, _ = _seed_course_with_memories(
        db_session, ["Prefers worked examples over abstract theory."], name="Course B"
    )
    query_embedding = embed_query("Can you show me a worked example instead of the theory?")

    results = retrieve_memories(db_session, other_course.id, query_embedding)

    retrieved_ids = {r.memory.id for r in results}
    assert memories[0].id not in retrieved_ids


def test_retrieval_bumps_access_count_and_last_accessed_at(db_session):
    course, memories = _seed_course_with_memories(
        db_session, ["Prefers worked examples over abstract theory."]
    )
    query_embedding = embed_query("Can you show me a worked example instead of the theory?")

    results = retrieve_memories(db_session, course.id, query_embedding)

    assert len(results) == 1
    db_session.refresh(memories[0])
    assert memories[0].access_count == 1
    assert memories[0].last_accessed_at is not None


def test_capped_at_limit(db_session):
    texts = [f"Prefers worked examples over abstract theory, variant {i}." for i in range(6)]
    course, memories = _seed_course_with_memories(db_session, texts)
    query_embedding = embed_query("Can you show me a worked example instead of the theory?")

    results = retrieve_memories(db_session, course.id, query_embedding, limit=3)

    assert len(results) == 3


def test_fresher_lower_similarity_memory_can_outrank_stale_higher_similarity_one(db_session):
    """Deterministic test of the decay-reranking behaviour (ADR 009): a
    memory with lower raw cosine similarity but much more recent activity
    should outrank one with higher similarity but 90 days stale. Uses
    hand-constructed unit vectors with exact known cosine similarity to
    the query, rather than real embeddings, so the similarity gap and the
    ranking outcome are both fully deterministic."""
    course = Course(name="Decay Rerank Test Course")
    db_session.add(course)
    db_session.flush()

    dim = 384
    e1 = [1.0] + [0.0] * (dim - 1)
    e2 = [0.0, 1.0] + [0.0] * (dim - 2)

    def blend(sim: float) -> list[float]:
        other = (1 - sim**2) ** 0.5
        return [sim * a + other * b for a, b in zip(e1, e2)]

    now = datetime.now(timezone.utc)
    stale_high_sim = Memory(
        course_id=course.id,
        content="stale but high similarity",
        embedding=blend(0.9),
        memory_type="preference",
        confidence=0.8,
        created_at=now - timedelta(days=90),
    )
    fresh_lower_sim = Memory(
        course_id=course.id,
        content="fresh but lower similarity",
        embedding=blend(0.6),
        memory_type="preference",
        confidence=0.8,
        created_at=now,
    )
    db_session.add_all([stale_high_sim, fresh_lower_sim])
    db_session.commit()

    results = retrieve_memories(db_session, course.id, e1, limit=2)

    assert len(results) == 2
    assert results[0].memory.id == fresh_lower_sim.id
    assert results[0].similarity < results[1].similarity  # confirms it's NOT just similarity order
