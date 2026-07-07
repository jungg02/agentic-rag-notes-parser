from typing import Iterator

import openai

from app.providers.base import AuthError, LLMMessage, LLMProvider, LLMProviderError, LLMResponse, RateLimited


class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str, model: str, base_url: str | None = None):
        self._client = openai.OpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    def _to_openai_messages(self, messages: list[LLMMessage], system: str | None) -> list[dict]:
        result = []
        if system:
            result.append({"role": "system", "content": system})
        result.extend({"role": m.role, "content": m.content} for m in messages)
        return result

    def generate(self, messages: list[LLMMessage], system: str | None = None, max_tokens: int = 2048) -> LLMResponse:
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                max_tokens=max_tokens,
                messages=self._to_openai_messages(messages, system),
            )
        except openai.AuthenticationError as exc:
            raise AuthError(str(exc)) from exc
        except openai.RateLimitError as exc:
            raise RateLimited(str(exc)) from exc
        except openai.APIError as exc:
            raise LLMProviderError(str(exc)) from exc

        choice = response.choices[0]
        return LLMResponse(
            text=choice.message.content or "",
            input_tokens=response.usage.prompt_tokens if response.usage else None,
            output_tokens=response.usage.completion_tokens if response.usage else None,
            stop_reason=choice.finish_reason,
        )

    def generate_stream(
        self, messages: list[LLMMessage], system: str | None = None, max_tokens: int = 2048
    ) -> Iterator[str]:
        try:
            stream = self._client.chat.completions.create(
                model=self._model,
                max_tokens=max_tokens,
                messages=self._to_openai_messages(messages, system),
                stream=True,
            )
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except openai.AuthenticationError as exc:
            raise AuthError(str(exc)) from exc
        except openai.RateLimitError as exc:
            raise RateLimited(str(exc)) from exc
        except openai.APIError as exc:
            raise LLMProviderError(str(exc)) from exc
