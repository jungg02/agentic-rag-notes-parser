"""Conversation-history compaction (Phase 1 of the retrieval upgrade plan, ADR 004).

Keeps the most recent HISTORY_KEEP_LAST_N messages verbatim. Once the full
session history exceeds HISTORY_TOKEN_BUDGET tokens, everything older than
the verbatim window gets folded into `chat_sessions.summary` via one
incremental LLM call -- only messages not already covered by
`summarized_through_message_id` are sent, appended onto the existing
summary rather than re-summarizing the whole history every turn.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session
from transformers import AutoTokenizer

from app.models import ChatMessage, ChatSession
from app.providers.base import LLMMessage, LLMProvider

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


def _summarize(provider: LLMProvider, previous_summary: str | None, new_messages: list[ChatMessage]) -> str:
    transcript = "\n".join(f"{m.role}: {m.content}" for m in new_messages)
    prompt = (
        f"Existing summary of earlier turns:\n{previous_summary}\n\nNew turns to fold in:\n{transcript}"
        if previous_summary
        else f"Conversation to summarize:\n{transcript}"
    )
    response = provider.generate(
        [LLMMessage(role="user", content=prompt)], system=_SUMMARY_SYSTEM_PROMPT, max_tokens=400
    )
    return response.text.strip()


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
        session.summary = _summarize(provider, session.summary, to_summarize)
        session.summarized_through_message_id = to_summarize[-1].id
        db.commit()

    result = []
    if session.summary:
        result.append(LLMMessage(role="user", content=f"[Summary of earlier conversation]\n{session.summary}"))
    result.extend(LLMMessage(role=m.role, content=m.content) for m in verbatim)
    return result
