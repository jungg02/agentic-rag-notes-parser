"""Semantic-memory retrieval (Phase 2, Task 3, ADR 005).

Relevance-thresholded and capped: only memories above
MEMORY_SIMILARITY_THRESHOLD are candidates at all, and at most
MEMORY_MAX_PER_QUERY are returned, ranked by similarity * decay_score --
so a highly-relevant-but-stale memory can lose to a somewhat-less-relevant
but fresh one, consistent with ADR 009's recency-based conflict
resolution instead of explicit contradiction detection. Retrieving a
memory bumps its access_count/last_accessed_at, feeding back into future
decay scoring and eviction (app/memory/decay.py).
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.memory.decay import decay_score
from app.models import Memory

MEMORY_SIMILARITY_THRESHOLD = 0.5
MEMORY_MAX_PER_QUERY = 3
_CANDIDATE_POOL_SIZE = 20


@dataclass
class ScoredMemory:
    memory: Memory
    similarity: float


def retrieve_memories(
    db: Session,
    course_id: int,
    query_embedding: list[float],
    limit: int = MEMORY_MAX_PER_QUERY,
    *,
    bump_access: bool = True,
) -> list[ScoredMemory]:
    """bump_access=False is for read-only observers (the inspection
    endpoint's search) that shouldn't perturb decay/eviction state just by
    looking -- the real chat flow always uses the default."""
    rows = db.execute(
        text(
            """
            SELECT id, 1 - (embedding <=> (:query_embedding)::vector) AS similarity
            FROM memories
            WHERE course_id = :course_id
            ORDER BY embedding <=> (:query_embedding)::vector
            LIMIT :pool_size
            """
        ),
        {"course_id": course_id, "query_embedding": str(query_embedding), "pool_size": _CANDIDATE_POOL_SIZE},
    ).all()

    above_threshold = [(row[0], row[1]) for row in rows if row[1] >= MEMORY_SIMILARITY_THRESHOLD]
    if not above_threshold:
        return []

    memory_ids = [mid for mid, _ in above_threshold]
    memories_by_id = {m.id: m for m in db.scalars(select(Memory).where(Memory.id.in_(memory_ids))).all()}

    now = datetime.now(timezone.utc)
    candidates = [
        ScoredMemory(memory=memories_by_id[mid], similarity=similarity)
        for mid, similarity in above_threshold
        if mid in memories_by_id
    ]
    candidates.sort(key=lambda c: c.similarity * decay_score(c.memory, now=now), reverse=True)
    top = candidates[:limit]

    if bump_access:
        for scored in top:
            scored.memory.access_count += 1
            scored.memory.last_accessed_at = now
        db.commit()

    return top
