"""Opportunistic end-of-session memory extraction (Phase 2, ADR 008).

No scheduler exists in this app, so staleness is checked at the one
request path where a brief pause is already expected: starting a new
session (see app/routers/chat.py). A session counts as "ended" once
SESSION_INACTIVITY_MINUTES have passed since its last message and
extraction hasn't already run through that message
(chat_sessions.memory_extracted_through_message_id).

extract_stale_sessions processes at most MAX_SESSIONS_PER_SWEEP stale
session(s) per call and stops there, regardless of outcome -- this bounds
the worst-case added latency on the triggering request to a fixed number
of provider round-trips instead of one per stale session in the course.
Sessions left unprocessed this call are picked up on a later one.

This piggybacks on a user-facing request, so a failure here must never
break the request that triggered it -- extraction failures for one
session are logged and skipped, not raised, and the watermark only
advances for sessions that actually succeeded (so a transient failure
gets retried on the next opportunity rather than silently skipped
forever). Note: two concurrent requests could both pick up the same
stale session before either advances the watermark, producing duplicate
memories -- not addressed here since MAX_SESSIONS_PER_SWEEP=1 makes it
rare in practice, and duplicate near-identical memories just compete on
decay score rather than corrupting anything.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.embedder import embed_texts
from app.memory.decay import enforce_memory_cap
from app.memory.extraction import MEMORY_CONFIDENCE_THRESHOLD, extract_memories
from app.models import ChatMessage, ChatSession, Memory
from app.providers.base import LLMProvider

logger = logging.getLogger(__name__)

SESSION_INACTIVITY_MINUTES = 30
MAX_SESSIONS_PER_SWEEP = 1


def _as_aware_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _is_stale_and_unextracted(session: ChatSession, last_message: ChatMessage, now: datetime) -> bool:
    age = now - _as_aware_utc(last_message.created_at)
    if age < timedelta(minutes=SESSION_INACTIVITY_MINUTES):
        return False
    return session.memory_extracted_through_message_id != last_message.id


def _messages_since_watermark(session: ChatSession, messages: list[ChatMessage]) -> list[ChatMessage]:
    if session.memory_extracted_through_message_id is None:
        return messages
    for i, m in enumerate(messages):
        if m.id == session.memory_extracted_through_message_id:
            return messages[i + 1 :]
    return messages


def _extract_one_session(db: Session, session: ChatSession, to_process: list[ChatMessage], provider: LLMProvider) -> None:
    candidates = extract_memories(provider, to_process)
    durable = [c for c in candidates if c.confidence >= MEMORY_CONFIDENCE_THRESHOLD]
    if durable:
        vectors = embed_texts([c.content for c in durable])
        for candidate, vector in zip(durable, vectors):
            db.add(
                Memory(
                    course_id=session.course_id,
                    content=candidate.content,
                    embedding=vector,
                    memory_type=candidate.memory_type,
                    confidence=candidate.confidence,
                    source_session_id=session.id,
                )
            )

    session.memory_extracted_through_message_id = to_process[-1].id
    db.commit()


def extract_stale_sessions(db: Session, course_id: int, provider: LLMProvider) -> int:
    """Checks sessions in this course for staleness and extracts memory
    from up to MAX_SESSIONS_PER_SWEEP of them, stopping after that many
    attempts (successful or not) so a caller on a request path never pays
    for more than a fixed number of provider round-trips. Returns the
    number of sessions successfully processed."""
    now = datetime.now(timezone.utc)
    # Ordered by id (creation order) so which session gets the sweep's one
    # attempt is deterministic and, since it approximates oldest-stale-first,
    # a reasonable default policy now that the cap makes selection matter.
    sessions = db.scalars(
        select(ChatSession).where(ChatSession.course_id == course_id).order_by(ChatSession.id)
    ).all()

    processed = 0
    attempted = 0
    for session in sessions:
        if attempted >= MAX_SESSIONS_PER_SWEEP:
            break

        messages = db.scalars(
            select(ChatMessage).where(ChatMessage.session_id == session.id).order_by(ChatMessage.created_at)
        ).all()
        if not messages:
            continue

        last_message = messages[-1]
        if not _is_stale_and_unextracted(session, last_message, now):
            continue

        to_process = _messages_since_watermark(session, messages)
        attempted += 1
        try:
            _extract_one_session(db, session, to_process, provider)
        except Exception:  # noqa: BLE001 - one session's extraction failure must not break the request that triggered it
            db.rollback()
            logger.warning("Memory extraction failed for session %s", session.id, exc_info=True)
            continue
        processed += 1

    if processed:
        enforce_memory_cap(db, course_id)

    return processed
