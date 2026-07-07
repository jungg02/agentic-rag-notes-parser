from typing import Iterator

import anthropic

from app.providers.base import AuthError, LLMMessage, LLMProvider, LLMProviderError, LLMResponse, RateLimited


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, model: str):
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def _to_anthropic_messages(self, messages: list[LLMMessage]) -> list[dict]:
        return [{"role": m.role, "content": m.content} for m in messages]

    def generate(self, messages: list[LLMMessage], system: str | None = None, max_tokens: int = 2048) -> LLMResponse:
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system or anthropic.NOT_GIVEN,
                messages=self._to_anthropic_messages(messages),
            )
        except anthropic.AuthenticationError as exc:
            raise AuthError(str(exc)) from exc
        except anthropic.RateLimitError as exc:
            raise RateLimited(str(exc)) from exc
        except anthropic.APIError as exc:
            raise LLMProviderError(str(exc)) from exc

        text = "".join(block.text for block in response.content if block.type == "text")
        return LLMResponse(
            text=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            stop_reason=response.stop_reason,
        )

    def generate_stream(
        self, messages: list[LLMMessage], system: str | None = None, max_tokens: int = 2048
    ) -> Iterator[str]:
        try:
            with self._client.messages.stream(
                model=self._model,
                max_tokens=max_tokens,
                system=system or anthropic.NOT_GIVEN,
                messages=self._to_anthropic_messages(messages),
            ) as stream:
                yield from stream.text_stream
        except anthropic.AuthenticationError as exc:
            raise AuthError(str(exc)) from exc
        except anthropic.RateLimitError as exc:
            raise RateLimited(str(exc)) from exc
        except anthropic.APIError as exc:
            raise LLMProviderError(str(exc)) from exc
