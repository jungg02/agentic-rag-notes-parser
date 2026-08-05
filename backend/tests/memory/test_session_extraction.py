import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

import app.memory.decay as decay
import app.memory.session_extraction as session_extraction
from app.memory.session_extraction import extract_stale_sessions
from app.models import ChatMessage, ChatSession, Course, Memory
from app.providers.base import LLMResponse


class FakeProvider:
    def __init__(self, reply_text: str = "[]"):
        self.reply_text = reply_text
        self.calls = 0

    def generate(self, messages, system=None, max_tokens=2048):
        self.calls += 1
        return LLMResponse(text=self.reply_text, input_tokens=10, output_tokens=10, stop_reason="end_turn")

    def generate_stream(self, messages, system=None, max_tokens=2048):
        raise NotImplementedError


class RaisingProvider(FakeProvider):
    def generate(self, messages, system=None, max_tokens=2048):
        self.calls += 1
        raise RuntimeError("simulated provider failure")


def _session_with_message(db_session, course, *, age_minutes: int) -> ChatSession:
    session = ChatSession(course_id=course.id)
    db_session.add(session)
    db_session.flush()
    created_at = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    message = ChatMessage(session_id=session.id, role="user", content="I prefer examples.", created_at=created_at)
    db_session.add(message)
    db_session.commit()
    return session


_ONE_MEMORY_REPLY = json.dumps(
    [{"memory_type": "preference", "content": "Prefers worked examples.", "confidence": 0.9}]
)


def test_stale_unextracted_session_gets_extracted(db_session):
    course = Course(name="Session Extraction Test Course")
    db_session.add(course)
    db_session.flush()
    session = _session_with_message(db_session, course, age_minutes=60)
    provider = FakeProvider(_ONE_MEMORY_REPLY)

    processed = extract_stale_sessions(db_session, course.id, provider)

    assert processed == 1
    assert provider.calls == 1
    db_session.refresh(session)
    assert session.memory_extracted_through_message_id is not None
    memories = db_session.scalars(select(Memory).where(Memory.course_id == course.id)).all()
    assert len(memories) == 1
    assert memories[0].content == "Prefers worked examples."
    assert memories[0].source_session_id == session.id


def test_fresh_session_not_extracted(db_session):
    course = Course(name="Fresh Session Test Course")
    db_session.add(course)
    db_session.flush()
    _session_with_message(db_session, course, age_minutes=5)
    provider = FakeProvider(_ONE_MEMORY_REPLY)

    processed = extract_stale_sessions(db_session, course.id, provider)

    assert processed == 0
    assert provider.calls == 0
    assert db_session.scalars(select(Memory).where(Memory.course_id == course.id)).all() == []


def test_already_extracted_session_not_reextracted(db_session):
    course = Course(name="Already Extracted Test Course")
    db_session.add(course)
    db_session.flush()
    session = _session_with_message(db_session, course, age_minutes=60)
    last_message = db_session.scalars(select(ChatMessage).where(ChatMessage.session_id == session.id)).one()
    session.memory_extracted_through_message_id = last_message.id
    db_session.commit()
    provider = FakeProvider(_ONE_MEMORY_REPLY)

    processed = extract_stale_sessions(db_session, course.id, provider)

    assert processed == 0
    assert provider.calls == 0


def test_low_confidence_extracted_memory_not_persisted(db_session):
    course = Course(name="Low Confidence Test Course")
    db_session.add(course)
    db_session.flush()
    _session_with_message(db_session, course, age_minutes=60)
    low_confidence_reply = json.dumps(
        [{"memory_type": "topic", "content": "Maybe interested in sorting?", "confidence": 0.2}]
    )
    provider = FakeProvider(low_confidence_reply)

    processed = extract_stale_sessions(db_session, course.id, provider)

    assert processed == 1  # session watermark still advances, nothing durable enough to persist
    assert db_session.scalars(select(Memory).where(Memory.course_id == course.id)).all() == []


def test_extraction_failure_for_one_session_does_not_break_others(real_db_session, monkeypatch):
    # Uses real_db_session, not db_session: this exercises a real
    # db.rollback() call inside session_extraction.py's exception handler,
    # which conflicts with db_session's SAVEPOINT-restart machinery (see
    # conftest.py's own documented distinction between the two fixtures).
    # Raises the per-call cap to 2 so both stale sessions are attempted in
    # this call -- otherwise MAX_SESSIONS_PER_SWEEP=1 would stop after the
    # first and this test wouldn't exercise the "others" part of its name.
    monkeypatch.setattr(session_extraction, "MAX_SESSIONS_PER_SWEEP", 2)
    course = Course(name="Partial Failure Test Course")
    real_db_session.add(course)
    real_db_session.flush()
    try:
        session_a = _session_with_message(real_db_session, course, age_minutes=60)
        _session_with_message(real_db_session, course, age_minutes=90)
        provider = RaisingProvider()

        processed = extract_stale_sessions(real_db_session, course.id, provider)

        assert processed == 0  # both fail (same raising provider), but no exception propagates
        assert provider.calls == 2  # both were attempted, independently
        real_db_session.refresh(session_a)
        assert session_a.memory_extracted_through_message_id is None  # watermark not advanced on failure
    finally:
        real_db_session.delete(course)
        real_db_session.commit()


def test_sweep_stops_after_max_sessions_per_sweep(db_session):
    course = Course(name="Sweep Cap Test Course")
    db_session.add(course)
    db_session.flush()
    _session_with_message(db_session, course, age_minutes=60)
    session_b = _session_with_message(db_session, course, age_minutes=90)
    provider = FakeProvider(_ONE_MEMORY_REPLY)

    processed = extract_stale_sessions(db_session, course.id, provider)

    assert processed == 1  # MAX_SESSIONS_PER_SWEEP=1 -- only one attempted this call
    assert provider.calls == 1
    db_session.refresh(session_b)
    assert session_b.memory_extracted_through_message_id is None  # left for a later call


def test_enforce_memory_cap_invoked_after_processing(db_session, monkeypatch):
    calls = []
    monkeypatch.setattr(session_extraction, "enforce_memory_cap", lambda db, course_id: calls.append(course_id))

    course = Course(name="Cap Invocation Test Course")
    db_session.add(course)
    db_session.flush()
    _session_with_message(db_session, course, age_minutes=60)
    provider = FakeProvider("[]")

    extract_stale_sessions(db_session, course.id, provider)

    assert calls == [course.id]


def test_enforce_memory_cap_not_invoked_when_nothing_processed(db_session, monkeypatch):
    calls = []
    monkeypatch.setattr(session_extraction, "enforce_memory_cap", lambda db, course_id: calls.append(course_id))

    course = Course(name="No Cap Invocation Test Course")
    db_session.add(course)
    db_session.flush()
    _session_with_message(db_session, course, age_minutes=5)  # fresh, nothing to process
    provider = FakeProvider("[]")

    extract_stale_sessions(db_session, course.id, provider)

    assert calls == []
