from dataclasses import dataclass
from typing import Iterator, Literal, Protocol, runtime_checkable


@dataclass
class LLMMessage:
    role: Literal["user", "assistant"]
    content: str


@dataclass
class LLMResponse:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    stop_reason: str | None


class LLMProviderError(Exception):
    pass


class RateLimited(LLMProviderError):
    pass


class AuthError(LLMProviderError):
    pass


@runtime_checkable
class LLMProvider(Protocol):
    def generate(
        self, messages: list[LLMMessage], system: str | None = None, max_tokens: int = 2048
    ) -> LLMResponse: ...

    def generate_stream(
        self, messages: list[LLMMessage], system: str | None = None, max_tokens: int = 2048
    ) -> Iterator[str]: ...
