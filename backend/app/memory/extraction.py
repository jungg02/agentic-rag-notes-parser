"""Semantic-memory extraction (Phase 2 of the retrieval upgrade plan, ADR 006).

Runs once per session at end-of-session (ADR 008), not per-turn -- the
caller (app/routers/chat.py's opportunistic staleness check) is
responsible for deciding *when* to call this and for persisting results
above MEMORY_CONFIDENCE_THRESHOLD; this module only turns a transcript into
validated candidate facts.
"""
import json
import logging
import re

from app.models import ChatMessage
from app.memory.schemas import VALID_MEMORY_TYPES, ExtractedMemory
from app.providers.base import LLMMessage, LLMProvider

logger = logging.getLogger(__name__)

MEMORY_CONFIDENCE_THRESHOLD = 0.6

_SYSTEM_PROMPT = """You are a memory-extraction step in a study-notes RAG system. Given a chat \
session between a student and their study assistant, extract any DURABLE facts about the \
student worth remembering in future sessions. Only extract something if it would still be true \
and useful weeks from now -- not a one-off detail specific to this single conversation.

Extract facts of these types only:
- topic: a subject/concept the student has studied or asked about repeatedly
- struggle: a concept the student found confusing or asked to have re-explained
- preference: a stated preference for how they like explanations (e.g. examples over theory, \
step-by-step over summary)
- goal: a recurring goal the student mentioned (e.g. preparing for an exam, building a project)

Do NOT extract: one-off questions, facts already obvious from the course material itself, or \
anything you are not confident is genuinely durable. If nothing in this conversation is worth \
remembering, return an empty list -- that is the correct answer most of the time.

Respond with ONLY a JSON array, no other text. Each element:
{"memory_type": "<topic|struggle|preference|goal>", "content": "<a short, self-contained \
statement>", "confidence": <0.0-1.0, your confidence this is genuinely durable>}

Example: [{"memory_type": "preference", "content": "Prefers worked examples over abstract \
explanations.", "confidence": 0.85}]"""

_JSON_ARRAY = re.compile(r"\[.*\]", re.DOTALL)


def _parse(raw_text: str) -> list[ExtractedMemory]:
    match = _JSON_ARRAY.search(raw_text)
    if not match:
        logger.warning("Memory extraction: no JSON array in model response: %r", raw_text[:200])
        return []

    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        logger.warning("Memory extraction: could not parse JSON array from model response: %r", raw_text[:200])
        return []

    if not isinstance(data, list):
        logger.warning("Memory extraction: model response was valid JSON but not a list: %r", data)
        return []

    results: list[ExtractedMemory] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        memory_type = item.get("memory_type")
        content = item.get("content")
        confidence = item.get("confidence")

        if memory_type not in VALID_MEMORY_TYPES:
            logger.warning("Memory extraction: skipping item with invalid memory_type %r", memory_type)
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            continue
        if not (0.0 <= confidence <= 1.0):
            continue

        results.append(ExtractedMemory(content=content.strip(), memory_type=memory_type, confidence=float(confidence)))
    return results


def extract_memories(provider: LLMProvider, messages: list[ChatMessage]) -> list[ExtractedMemory]:
    if not messages:
        return []

    transcript = "\n".join(f"{m.role}: {m.content}" for m in messages)
    response = provider.generate(
        [LLMMessage(role="user", content=f"Session transcript:\n{transcript}")],
        system=_SYSTEM_PROMPT,
        max_tokens=1500,
    )
    return _parse(response.text)
