from types import SimpleNamespace

from app.providers.base import LLMMessage
from app.providers.openai_provider import OpenAIProvider


def _chunk(content=None):
    if content is None:
        # Some OpenAI-compatible gateways emit chunks with an empty
        # `choices` list (e.g. a trailing usage-only chunk) - the streaming
        # loop must skip these rather than indexing into an empty list.
        return SimpleNamespace(choices=[])
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content))])


def test_generate_stream_skips_chunks_with_empty_choices(monkeypatch):
    provider = OpenAIProvider(api_key="fake-key", model="big-pickle")

    fake_stream = [_chunk("Hello"), _chunk(), _chunk(" world"), _chunk(None)]
    monkeypatch.setattr(
        provider._client.chat.completions, "create", lambda **kwargs: iter(fake_stream)
    )

    deltas = list(provider.generate_stream([LLMMessage(role="user", content="hi")]))
    assert deltas == ["Hello", " world"]
