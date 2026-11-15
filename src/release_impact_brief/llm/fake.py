"""A provider that never calls out: canned replies, so tests are free and repeatable."""

import json
from collections.abc import Callable, Sequence
from typing import Any

from release_impact_brief.llm.base import LlmRequest, LlmResponse, Usage


def _placeholder(schema: dict[str, Any]) -> Any:
    """The simplest value that matches a JSON schema (strings say they're fake)."""
    if "enum" in schema:
        return schema["enum"][0]
    match schema.get("type"):
        case "object":
            return {k: _placeholder(v) for k, v in schema.get("properties", {}).items()}
        case "array":
            return []
        case "boolean":
            return False
        case "integer" | "number":
            return 0
    return "(fake answer: no LLM was called)"


def _default_reply(request: LlmRequest) -> str:
    return json.dumps(_placeholder(request.schema)) if request.schema else "(fake answer)"


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


class FakeProvider:
    def __init__(self, reply: str | Callable[[LlmRequest], str] = _default_reply,
                 model: str = "fake") -> None:  # fmt: skip
        self.model = model
        self.reply = reply
        self.calls: list[LlmRequest] = []
        self.counted = 0  # how many times count_tokens was asked

    def count_tokens(self, requests: Sequence[LlmRequest]) -> int:
        self.counted += 1
        return sum(_tokens(r.system + r.prompt) for r in requests)

    def complete(self, request: LlmRequest) -> LlmResponse:
        self.calls.append(request)
        text = self.reply(request) if callable(self.reply) else self.reply  # may raise LlmError
        usage = Usage(_tokens(request.system + request.prompt), _tokens(text))
        return LlmResponse(text, usage, self.model)
