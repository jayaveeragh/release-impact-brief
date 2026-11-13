"""A provider that never calls out: canned replies, so tests are free and repeatable."""

from collections.abc import Callable

from release_impact_brief.llm.base import LlmRequest, LlmResponse, Usage


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


class FakeProvider:
    def __init__(self, reply: str | Callable[[LlmRequest], str] = "{}",
                 model: str = "fake") -> None:  # fmt: skip
        self.model = model
        self.reply = reply
        self.calls: list[LlmRequest] = []
        self.counted = 0  # how many times count_tokens was asked

    def count_tokens(self, request: LlmRequest) -> int:
        self.counted += 1
        return _tokens(request.system + request.prompt)

    def complete(self, request: LlmRequest) -> LlmResponse:
        self.calls.append(request)
        text = self.reply(request) if callable(self.reply) else self.reply
        usage = Usage(_tokens(request.system + request.prompt), _tokens(text))
        return LlmResponse(text, usage, self.model)
