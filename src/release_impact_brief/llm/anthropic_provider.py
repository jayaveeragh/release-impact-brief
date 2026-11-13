"""Claude through the official Anthropic SDK.

The system prompt is marked for prompt caching (the same instructions are sent
for every summary), the effort level is explicit, `schema` becomes a structured
output so the reply is guaranteed JSON, and server-side fallbacks retry a request
the safety classifiers decline on the model Anthropic recommends for that case.
"""

from typing import Any

import anthropic

from release_impact_brief.llm.base import LlmRefusal, LlmRequest, LlmResponse, LlmTruncated, Usage

_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider:
    def __init__(self, model: str, effort: str, client: Any = None) -> None:
        self.model = model
        self.effort = effort
        # The SDK reads ANTHROPIC_API_KEY itself; the key never passes through our code.
        # Typed as Any: tests pass a stub client; the request shape is checked by tests.
        self.client: Any = client if client is not None else anthropic.Anthropic()

    @staticmethod
    def _format(request: LlmRequest) -> dict[str, Any]:
        if request.schema is None:
            return {}
        return {"format": {"type": "json_schema", "schema": request.schema}}

    def complete(self, request: LlmRequest) -> LlmResponse:
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=request.max_tokens,
            system=[
                {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
            ],  # fmt: skip
            messages=[{"role": "user", "content": request.prompt}],
            output_config={"effort": self.effort, **self._format(request)},
            betas=[_FALLBACK_BETA],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            details = response.stop_details
            raise LlmRefusal(f"declined ({getattr(details, 'category', None) or 'no category'})")
        if response.stop_reason == "max_tokens":
            raise LlmTruncated(f"reply hit max_tokens={request.max_tokens}")
        text = "".join(block.text for block in response.content if block.type == "text")
        u = response.usage
        usage = Usage(u.input_tokens, u.output_tokens, u.cache_creation_input_tokens or 0,
                      u.cache_read_input_tokens or 0)  # fmt: skip
        return LlmResponse(text, usage, response.model)

    def count_tokens(self, request: LlmRequest) -> int:
        counted = self.client.messages.count_tokens(
            model=self.model,
            system=request.system,
            messages=[{"role": "user", "content": request.prompt}],
            **({"output_config": self._format(request)} if request.schema else {}),
        )
        return int(counted.input_tokens)
