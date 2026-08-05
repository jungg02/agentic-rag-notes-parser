from datetime import datetime, timedelta, timezone

import app.memory.decay as decay
from app.models import Course, Memory


def _memory(**overrides) -> Memory:
    defaults = dict(
        course_id=1,
        content="test",
        embedding=[0.01] * 384,
        memory_type="topic",
        confidence=0.8,
        access_count=0,
        last_accessed_at=None,
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return Memory(**defaults)


def test_decay_score_higher_for_more_recent_memory():
    now = datetime.now(timezone.utc)
    recent = _memory(created_at=now - timedelta(days=1))
    old = _memory(created_at=now - timedelta(days=60))
    assert decay.decay_score(recent, now=now) > decay.decay_score(old, now=now)


def test_decay_score_higher_for_more_accessed_memory():
    now = datetime.now(timezone.utc)
    accessed = _memory(created_at=now - timedelta(days=5), access_count=10)
    unaccessed = _memory(created_at=now - timedelta(days=5), access_count=0)
    assert decay.decay_score(accessed, now=now) > decay.decay_score(unaccessed, now=now)


def test_decay_score_higher_for_higher_confidence():
    now = datetime.now(timezone.utc)
    confident = _memory(created_at=now, confidence=0.95)
    unsure = _memory(created_at=now, confidence=0.4)
    assert decay.decay_score(confident, now=now) > decay.decay_score(unsure, now=now)


def test_decay_score_prefers_last_accessed_over_created_at():
    now = datetime.now(timezone.utc)
    # created long ago but touched recently -- should score like "recent"
    touched_recently = _memory(
        created_at=now - timedelta(days=90), last_accessed_at=now - timedelta(days=1)
    )
    never_touched = _memory(created_at=now - timedelta(days=90), last_accessed_at=None)
    assert decay.decay_score(touched_recently, now=now) > decay.decay_score(never_touched, now=now)


def test_decay_score_handles_naive_datetimes_without_raising():
    now_naive = datetime.now()  # no tzinfo
    memory = _memory(created_at=now_naive - timedelta(days=2))
    score = decay.decay_score(memory, now=now_naive)
    assert score > 0


def test_enforce_memory_cap_noop_when_under_cap(db_session, monkeypatch):
    monkeypatch.setattr(decay, "MEMORY_MAX_PER_COURSE", 5)
    course = Course(name="Decay Cap Test Course")
    db_session.add(course)
    db_session.flush()
    for i in range(3):
        db_session.add(_memory(course_id=course.id, content=f"m{i}"))
    db_session.flush()

    deleted = decay.enforce_memory_cap(db_session, course.id)

    assert deleted == 0


def test_enforce_memory_cap_deletes_lowest_scoring_when_over_cap(db_session, monkeypatch):
    monkeypatch.setattr(decay, "MEMORY_MAX_PER_COURSE", 2)
    course = Course(name="Decay Cap Overflow Test Course")
    db_session.add(course)
    db_session.flush()

    now = datetime.now(timezone.utc)
    high = _memory(course_id=course.id, content="high", created_at=now, confidence=0.95, access_count=5)
    mid = _memory(course_id=course.id, content="mid", created_at=now - timedelta(days=10), confidence=0.7)
    low = _memory(course_id=course.id, content="low", created_at=now - timedelta(days=90), confidence=0.3)
    db_session.add_all([high, mid, low])
    db_session.flush()

    deleted = decay.enforce_memory_cap(db_session, course.id)

    from sqlalchemy import select

    remaining_contents = {m.content for m in db_session.scalars(select(Memory).where(Memory.course_id == course.id)).all()}
    assert deleted == 1
    assert remaining_contents == {"high", "mid"}
    assert "low" not in remaining_contents


def test_enforce_memory_cap_scoped_to_course(db_session, monkeypatch):
    monkeypatch.setattr(decay, "MEMORY_MAX_PER_COURSE", 1)
    course_a = Course(name="Cap Scope Course A")
    course_b = Course(name="Cap Scope Course B")
    db_session.add_all([course_a, course_b])
    db_session.flush()
    db_session.add(_memory(course_id=course_a.id, content="a1"))
    db_session.add(_memory(course_id=course_b.id, content="b1"))
    db_session.flush()

    deleted = decay.enforce_memory_cap(db_session, course_a.id)

    assert deleted == 0  # course_a already at cap of 1, course_b untouched regardless
