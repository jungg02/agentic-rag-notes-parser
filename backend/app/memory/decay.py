"""Memory decay and eviction (Phase 2, Task 6, ADR 009).

One decay score serves two purposes: ranking retrieved memory candidates
(app/memory/retrieval.py) and evicting the lowest-scoring memories when a
course exceeds MEMORY_MAX_PER_COURSE. Reusing the same score for both is
what lets conflict handling work without explicit contradiction detection
(ADR 009): a newer, more-accessed fact naturally outranks a stale one at
query time *and* is what survives eviction -- no separate "does this
contradict that" step needed.
"""
import math
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Memory

MEMORY_MAX_PER_COURSE = 50
DECAY_HALF_LIFE_DAYS = 30.0


def _as_aware_utc(dt: datetime) -> datetime:
    # created_at/last_accessed_at may come back naive or aware depending on
    # how the table was created (see the schema-migration note in the Phase
    # 2 schema commit) -- normalize rather than assume, since a raw
    # naive-vs-aware subtraction raises TypeError.
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def decay_score(memory: Memory, *, now: datetime | None = None) -> float:
    """Higher = more relevant / more likely to survive eviction. Combines:
    - confidence: how sure extraction was this is a genuine durable fact
    - access_count: how often retrieval has actually surfaced it (a signal
      it keeps being useful, not just extracted once and never relevant)
    - recency: time since last touched (last_accessed_at if it's ever been
      retrieved, else created_at), exponentially decayed with a
      DECAY_HALF_LIFE_DAYS half-life
    """
    now = _as_aware_utc(now) if now is not None else datetime.now(timezone.utc)
    last_touch = _as_aware_utc(memory.last_accessed_at or memory.created_at)
    age_days = max((now - last_touch).total_seconds() / 86400, 0.0)
    recency_factor = math.exp(-age_days / DECAY_HALF_LIFE_DAYS)
    return memory.confidence * (memory.access_count + 1) * recency_factor


def enforce_memory_cap(db: Session, course_id: int) -> int:
    """Deletes the lowest-decay-scoring memories for a course until it's at
    or under MEMORY_MAX_PER_COURSE. Returns the number deleted."""
    memories = db.scalars(select(Memory).where(Memory.course_id == course_id)).all()
    overflow = len(memories) - MEMORY_MAX_PER_COURSE
    if overflow <= 0:
        return 0

    now = datetime.now(timezone.utc)
    lowest_scoring = sorted(memories, key=lambda m: decay_score(m, now=now))[:overflow]
    for memory in lowest_scoring:
        db.delete(memory)
    db.commit()
    return len(lowest_scoring)
