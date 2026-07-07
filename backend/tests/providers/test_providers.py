import os

import pytest

from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import LLMMessage


@pytest.mark.skipif(not os.environ.get("LLM_API_KEY"), reason="requires a real Anthropic API key")
def test_anthropic_generate_returns_text():
    provider = AnthropicProvider(api_key=os.environ["LLM_API_KEY"], model="claude-opus-4-8")
    response = provider.generate([LLMMessage(role="user", content="Say the word 'pong' and nothing else.")])
    assert "pong" in response.text.lower()
    assert response.stop_reason is not None


def test_anthropic_provider_implements_protocol():
    from app.providers.base import LLMProvider

    provider = AnthropicProvider(api_key="fake-key", model="claude-opus-4-8")
    assert isinstance(provider, LLMProvider)


def test_factory_returns_anthropic_provider_by_default(monkeypatch):
    from app.config import get_settings
    from app.providers.factory import get_provider

    get_settings.cache_clear()
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_API_KEY", "fake-key")
    monkeypatch.setenv("LLM_MODEL", "claude-opus-4-8")

    provider = get_provider()
    assert provider.__class__.__name__ == "AnthropicProvider"
    get_settings.cache_clear()
