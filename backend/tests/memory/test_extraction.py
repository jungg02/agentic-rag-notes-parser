import json
from types import SimpleNamespace

from app.memory.extraction import extract_memories
from app.providers.base import LLMResponse


class FakeProvider:
    def __init__(self, reply_text: str):
        self._reply_text = reply_text
        self.call_count = 0

    def generate(self, messages, system=None, max_tokens=2048):
        self.call_count += 1
        return LLMResponse(text=self._reply_text, input_tokens=10, output_tokens=10, stop_reason="end_turn")

    def generate_stream(self, messages, system=None, max_tokens=2048):
        raise NotImplementedError


def _msg(role: str, content: str):
    return SimpleNamespace(role=role, content=content)


def test_extracts_valid_memories_with_confidence():
    reply = json.dumps(
        [
            {"memory_type": "preference", "content": "Prefers worked examples over theory.", "confidence": 0.85},
            {"memory_type": "struggle", "content": "Struggles with recursion.", "confidence": 0.7},
        ]
    )
    provider = FakeProvider(reply)
    messages = [_msg("user", "I always get confused by recursion, can you use an example instead of theory?")]

    results = extract_memories(provider, messages)

    assert len(results) == 2
    assert results[0].memory_type == "preference"
    assert results[0].confidence == 0.85
    assert results[1].memory_type == "struggle"


def test_empty_list_response_returns_no_memories():
    provider = FakeProvider("[]")
    results = extract_memories(provider, [_msg("user", "What is BM25?")])
    assert results == []


def test_empty_messages_returns_empty_without_calling_provider():
    provider = FakeProvider("[]")
    results = extract_memories(provider, [])
    assert results == []
    assert provider.call_count == 0


def test_malformed_json_falls_back_to_empty_list():
    provider = FakeProvider("I don't think there's anything to remember here.")
    results = extract_memories(provider, [_msg("user", "hi")])
    assert results == []


def test_invalid_memory_type_is_skipped_but_valid_ones_kept():
    reply = json.dumps(
        [
            {"memory_type": "not_a_real_type", "content": "junk", "confidence": 0.9},
            {"memory_type": "goal", "content": "Preparing for a final exam in three weeks.", "confidence": 0.75},
        ]
    )
    provider = FakeProvider(reply)
    results = extract_memories(provider, [_msg("user", "hi")])
    assert len(results) == 1
    assert results[0].memory_type == "goal"


def test_confidence_out_of_range_is_skipped():
    reply = json.dumps([{"memory_type": "topic", "content": "Sorting algorithms", "confidence": 1.5}])
    provider = FakeProvider(reply)
    results = extract_memories(provider, [_msg("user", "hi")])
    assert results == []


def test_boolean_confidence_is_skipped():
    reply = json.dumps([{"memory_type": "topic", "content": "Sorting algorithms", "confidence": True}])
    provider = FakeProvider(reply)
    results = extract_memories(provider, [_msg("user", "hi")])
    assert results == []


def test_non_string_content_is_skipped():
    reply = json.dumps([{"memory_type": "topic", "content": 42, "confidence": 0.9}])
    provider = FakeProvider(reply)
    results = extract_memories(provider, [_msg("user", "hi")])
    assert results == []


def test_response_wrapped_in_markdown_code_fence_still_parses():
    reply = "```json\n" + json.dumps(
        [{"memory_type": "topic", "content": "Data frame sorting in R", "confidence": 0.8}]
    ) + "\n```"
    provider = FakeProvider(reply)
    results = extract_memories(provider, [_msg("user", "hi")])
    assert len(results) == 1
    assert results[0].content == "Data frame sorting in R"
