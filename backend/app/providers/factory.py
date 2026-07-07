from app.config import get_settings
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import LLMProvider
from app.providers.openai_provider import OpenAIProvider

_REGISTRY = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
}


def get_provider() -> LLMProvider:
    settings = get_settings()
    provider_cls = _REGISTRY.get(settings.llm_provider)
    if provider_cls is None:
        raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider}")

    if settings.llm_provider == "openai":
        return OpenAIProvider(api_key=settings.llm_api_key, model=settings.llm_model, base_url=settings.llm_base_url)
    return AnthropicProvider(api_key=settings.llm_api_key, model=settings.llm_model)
