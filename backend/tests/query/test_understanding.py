import json

from app.providers.base import LLMMessage, LLMResponse
from app.query.understanding import understand_query


class FakeProvider:
    def __init__(self, reply_text: str):
        self._reply_text = reply_text

    def generate(self, messages, system=None, max_tokens=2048):
        return LLMResponse(text=self._reply_text, input_tokens=10, output_tokens=10, stop_reason="end_turn")

    def generate_stream(self, messages, system=None, max_tokens=2048):
        raise NotImplementedError


def _reply(intent: str, needs_rewrite: bool, standalone_query: str | None = None) -> str:
    return json.dumps({"intent": intent, "needs_rewrite": needs_rewrite, "standalone_query": standalone_query})


def test_pure_followup_gets_rewritten_and_classified():
    history = [
        LLMMessage(role="user", content="What's the difference between BM25 and dense retrieval?"),
        LLMMessage(role="assistant", content="BM25 is lexical, dense retrieval uses embeddings."),
    ]
    provider = FakeProvider(
        _reply("follow_up", True, "Does BM25 or dense retrieval handle typos better?")
    )

    result = understand_query(provider, history, "Which one handles typos better?")

    assert result.intent == "follow_up"
    assert result.rewritten_query == "Does BM25 or dense retrieval handle typos better?"
    assert result.original_query == "Which one handles typos better?"
    assert result.retrieval_query == "Does BM25 or dense retrieval handle typos better?"


def test_standalone_query_mid_session_is_not_rewritten():
    history = [
        LLMMessage(role="user", content="What's the difference between BM25 and dense retrieval?"),
        LLMMessage(role="assistant", content="BM25 is lexical, dense retrieval uses embeddings."),
    ]
    provider = FakeProvider(_reply("factual_lookup", False, None))

    result = understand_query(provider, history, "What is a Postgres GIN index?")

    assert result.intent == "factual_lookup"
    assert result.rewritten_query is None
    assert result.retrieval_query == "What is a Postgres GIN index?"


def test_topic_switch_mid_session_is_not_rewritten():
    history = [
        LLMMessage(role="user", content="What's the difference between BM25 and dense retrieval?"),
        LLMMessage(role="assistant", content="BM25 is lexical, dense retrieval uses embeddings."),
        LLMMessage(role="user", content="Which one handles typos better?"),
        LLMMessage(role="assistant", content="Dense retrieval generally handles typos better."),
    ]
    provider = FakeProvider(_reply("summarization", False, None))

    result = understand_query(provider, history, "Summarize chapter 4 on sorting algorithms.")

    assert result.intent == "summarization"
    assert result.rewritten_query is None


def test_first_turn_has_no_history_and_is_not_rewritten():
    provider = FakeProvider(_reply("factual_lookup", False, None))

    result = understand_query(provider, [], "What is quicksort's average time complexity?")

    assert result.rewritten_query is None
    assert result.retrieval_query == "What is quicksort's average time complexity?"


def test_malformed_json_falls_back_to_factual_lookup_no_rewrite():
    provider = FakeProvider("Sorry, I can't help with that today!")

    result = understand_query(provider, [], "which one is faster?")

    assert result.intent == "factual_lookup"
    assert result.rewritten_query is None
    assert result.original_query == "which one is faster?"


def test_invalid_intent_label_falls_back_to_factual_lookup():
    provider = FakeProvider(_reply("not_a_real_intent", False, None))

    result = understand_query(provider, [], "what is BM25?")

    assert result.intent == "factual_lookup"


def test_non_string_standalone_query_is_ignored():
    provider = FakeProvider(json.dumps({"intent": "follow_up", "needs_rewrite": True, "standalone_query": 42}))

    result = understand_query(provider, [], "which one?")

    assert result.rewritten_query is None
