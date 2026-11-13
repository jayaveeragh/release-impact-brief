"""What every LLM provider takes and returns, independent of the vendor."""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class LlmRequest:
    system: str
    prompt: str
    max_tokens: int
    schema: dict[str, Any] | None = None  # JSON schema: the reply must be JSON matching it


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass(frozen=True)
class LlmResponse:
    text: str
    usage: Usage
    model: str  # the model that answered (can differ from the one asked after a fallback)


class LlmError(Exception):
    pass


class LlmRefusal(LlmError):
    """The model (and any fallback) declined the request."""


class LlmTruncated(LlmError):
    """The reply hit max_tokens, so it is incomplete."""


class Provider(Protocol):
    model: str

    def complete(self, request: LlmRequest) -> LlmResponse: ...

    def count_tokens(self, request: LlmRequest) -> int: ...
