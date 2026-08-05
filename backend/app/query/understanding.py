"""Query understanding (Phase 1 of the retrieval upgrade plan).

Classifies intent and, only when the latest message can't be understood on
its own, rewrites it into a standalone query -- both from a single LLM call
(ADR 002: one round-trip, not two). See docs/adr/001-002.
"""
import json
import logging
import re

from app.providers.base import LLMMessage, LLMProvider
from app.query.schemas import VALID_INTENTS, QueryUnderstanding

logger = logging.getLogger(__name__)

# How many prior turns to show the understanding call for coreference
# resolution. Independent of generation's own history/compaction budget
# (app/query/compaction.py) -- resolving "which one" only needs the last
# couple of exchanges, not the whole session.
HISTORY_WINDOW = 6

_SYSTEM_PROMPT = """You are the query-understanding step in a study-notes RAG system. Given the \
recent conversation history and the user's latest message, do two things:

1. Classify the latest message's intent as exactly one of: factual_lookup, comparison, \
summarization, follow_up.
2. Decide whether the latest message can be understood on its own, with no conversation \
history at all -- no pronouns ("it", "that one", "the second one"), no dropped subject, no \
implicit reference to something discussed earlier. If it can be understood on its own, \
needs_rewrite is false. If it cannot, needs_rewrite is true and standalone_query must be a \
rewritten version that preserves the original meaning while resolving those references using \
the history.

Respond with ONLY a JSON object and nothing else:
{"intent": "<one of the four labels>", "needs_rewrite": true or false, "standalone_query": \
"<rewritten query, or null when needs_rewrite is false>"}"""

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def _format_history(history: list[LLMMessage]) -> str:
    if not history:
        return "(this is the first message in the session)"
    return "\n".join(f"{m.role}: {m.content}" for m in history[-HISTORY_WINDOW:])


def _fallback(original_query: str) -> QueryUnderstanding:
    return QueryUnderstanding(original_query=original_query, intent="factual_lookup", rewritten_query=None)


def _parse(raw_text: str, original_query: str) -> QueryUnderstanding:
    match = _JSON_OBJECT.search(raw_text)
    if not match:
        logger.warning("Query understanding: no JSON object in model response: %r", raw_text)
        return _fallback(original_query)

    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        logger.warning("Query understanding: could not parse JSON from model response: %r", raw_text)
        return _fallback(original_query)

    intent = data.get("intent")
    if intent not in VALID_INTENTS:
        logger.warning("Query understanding: model returned invalid intent %r", intent)
        intent = "factual_lookup"

    rewritten_query = data.get("standalone_query") if data.get("needs_rewrite") else None
    if rewritten_query is not None and not isinstance(rewritten_query, str):
        rewritten_query = None

    return QueryUnderstanding(original_query=original_query, intent=intent, rewritten_query=rewritten_query)


def understand_query(provider: LLMProvider, history: list[LLMMessage], user_content: str) -> QueryUnderstanding:
    prompt = f"Conversation so far:\n{_format_history(history)}\n\nLatest user message:\n{user_content}"
    # Some providers (reasoning models) spend hidden tokens before emitting
    # the visible JSON -- those still count against max_tokens, so this
    # needs real headroom or the response truncates mid-JSON and the
    # (real) rewrite gets silently discarded by _parse's fallback below.
    # Measured against openai/gpt-oss-20b: ~400 output tokens for a ~30-word
    # JSON reply.
    response = provider.generate([LLMMessage(role="user", content=prompt)], system=_SYSTEM_PROMPT, max_tokens=1500)
    return _parse(response.text, user_content)
