"""Conversation-history compaction (Phase 1 of the retrieval upgrade plan, ADR 004).

Keeps the most recent HISTORY_KEEP_LAST_N messages verbatim. Once the full
session history exceeds HISTORY_TOKEN_BUDGET tokens, everything older than
the verbatim window gets folded into `chat_sessions.summary` via one
incremental LLM call -- only messages not already covered by
`summarized_through_message_id` are sent, appended onto the existing
summary rather than re-summarizing the whole history every turn.
"""
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session
from transformers import AutoTokenizer

from app.models import ChatMessage, ChatSession
from app.providers.base import LLMMessage, LLMProvider

logger = logging.getLogger(__name__)

HISTORY_TOKEN_BUDGET = 2000
HISTORY_KEEP_LAST_N = 6

_SUMMARY_SYSTEM_PROMPT = """Summarize the following study-notes chat conversation in a few \
sentences. Preserve the topics discussed, key facts or answers given, and anything the user \
asked to revisit or seemed confused about. Be concise -- this summary stands in for the full \
conversation in later turns, so keep whatever a later question might need to refer back to."""

_TOKENIZER = None


def _tokenizer() -> AutoTokenizer:
    global _TOKENIZER
    if _TOKENIZER is None:
        _TOKENIZER = AutoTokenizer.from_pretrained("BAAI/bge-small-en-v1.5")
    return _TOKENIZER


def _token_count(text: str) -> int:
    return len(_tokenizer().encode(text, add_special_tokens=False))


def _summarize(provider: LLMProvider, previous_summary: str | None, new_messages: list[ChatMessage]) -> str | None:
    """Returns None (rather than an empty/truncated string) when the model's
    response can't be trusted -- see the same headroom note in
    app/query/understanding.py: a reasoning provider can spend its whole
    max_tokens budget on hidden tokens and return nothing visible. The
    caller must treat None as "summarization did not happen this turn" and
    leave session.summary / summarized_through_message_id untouched, not
    persist an empty summary and advance the high-water mark past messages
    that were never actually folded in -- that would discard them for good,
    since they'll have aged out of the verbatim window by the next turn."""
    transcript = "\n".join(f"{m.role}: {m.content}" for m in new_messages)
    prompt = (
        f"Existing summary of earlier turns:\n{previous_summary}\n\nNew turns to fold in:\n{transcript}"
        if previous_summary
        else f"Conversation to summarize:\n{transcript}"
    )
    response = provider.generate(
        [LLMMessage(role="user", content=prompt)], system=_SUMMARY_SYSTEM_PROMPT, max_tokens=1500
    )
    text = response.text.strip()
    if not text:
        logger.warning("Compaction: summarization call returned no usable text; leaving summary unchanged")
        return None
    return text


def get_working_history(db: Session, session: ChatSession, provider: LLMProvider) -> list[LLMMessage]:
    """The message list to send to the generation call: a leading synthetic
    summary turn (once compaction has ever triggered) followed by verbatim
    messages. Persists an updated session.summary /
    summarized_through_message_id when compaction newly triggers."""
    messages = db.scalars(
        select(ChatMessage).where(ChatMessage.session_id == session.id).order_by(ChatMessage.created_at)
    ).all()

    total_tokens = sum(_token_count(m.content) for m in messages)
    if total_tokens <= HISTORY_TOKEN_BUDGET or len(messages) <= HISTORY_KEEP_LAST_N:
        return [LLMMessage(role=m.role, content=m.content) for m in messages]

    verbatim_cutoff = len(messages) - HISTORY_KEEP_LAST_N
    verbatim = messages[verbatim_cutoff:]

    already_summarized_index = 0
    if session.summarized_through_message_id is not None:
        for i, m in enumerate(messages):
            if m.id == session.summarized_through_message_id:
                already_summarized_index = i + 1
                break

    to_summarize = messages[already_summarized_index:verbatim_cutoff]
    if to_summarize:
        new_summary = _summarize(provider, session.summary, to_summarize)
        if new_summary is not None:
            session.summary = new_summary
            session.summarized_through_message_id = to_summarize[-1].id
            db.commit()
        # else: leave state untouched so the next turn retries summarizing
        # these same messages, instead of committing an empty/lossy summary.

    result = []
    if session.summary:
        result.append(LLMMessage(role="user", content=f"[Summary of earlier conversation]\n{session.summary}"))
    result.extend(LLMMessage(role=m.role, content=m.content) for m in verbatim)
    return result
