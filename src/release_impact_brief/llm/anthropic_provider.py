"""Claude through the official Anthropic SDK.

The system prompt is marked for prompt caching (the same instructions are sent
for every summary), the effort level is explicit, `schema` becomes a structured
output so the reply is guaranteed JSON, and server-side fallbacks retry a request
the safety classifiers decline on the model Anthropic recommends for that case.
"""

import json
from collections.abc import Sequence
from typing import Any

import anthropic

from release_impact_brief.llm.base import (
    LlmAuthError,
    LlmRefusal,
    LlmRequest,
    LlmResponse,
    LlmTruncated,
    Usage,
)

_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_CHUNK_CHARS = 600_000  # roughly 150-200k tokens per counting call, well under the context
_PLAIN = LlmRequest("", ".", 1)


class AnthropicProvider:
    def __init__(self, model: str, effort: str, client: Any = None) -> None:
        self.model = model
        self.effort = effort
        # The SDK reads ANTHROPIC_API_KEY itself; the key never passes through our code.
        # Typed as Any: tests pass a stub client; the request shape is checked by tests.
        self.client: Any = client if client is not None else anthropic.Anthropic()

    @staticmethod
    def _call(method: Any, **kwargs: Any) -> Any:
        try:
            return method(**kwargs)
        except anthropic.AuthenticationError:
            raise LlmAuthError(
                "the API key was rejected: check ANTHROPIC_API_KEY in .env"
            ) from None
        except TypeError as error:  # the SDK's "Could not resolve authentication method"
            if "authentication" not in str(error):
                raise
            raise LlmAuthError(
                "no Anthropic credentials: put ANTHROPIC_API_KEY in .env (see .env.example), "
                "or use RIB_MODEL=fake to try it without the API"
            ) from None

    @staticmethod
    def _format(request: LlmRequest) -> dict[str, Any]:
        if request.schema is None:
            return {}
        return {"format": {"type": "json_schema", "schema": request.schema}}

    def complete(self, request: LlmRequest) -> LlmResponse:
        response = self._call(
            self.client.beta.messages.create,
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

    def _count(self, system: str, prompt: str, sample: LlmRequest) -> int:
        counted = self._call(
            self.client.messages.count_tokens,
            model=self.model,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            **({"output_config": self._format(sample)} if sample.schema else {}),
        )
        return int(counted.input_tokens)

    def count_tokens(self, requests: Sequence[LlmRequest]) -> int:
        """Count many requests in few calls: prompts sharing a system prompt and schema
        are joined into one message per chunk, and the shared part (system prompt,
        schema) is added once per extra request. Close to, not exactly, the sum of
        separate counts (the joins add a few tokens)."""
        groups: dict[tuple[str, str], list[LlmRequest]] = {}
        for r in requests:
            groups.setdefault((r.system, json.dumps(r.schema, sort_keys=True)), []).append(r)
        total = 0
        for (system, _), group in groups.items():
            chunks: list[list[LlmRequest]] = [[]]
            for r in group:
                if (
                    chunks[-1]
                    and sum(len(c.prompt) for c in chunks[-1]) + len(r.prompt) > _CHUNK_CHARS
                ):
                    chunks.append([])
                chunks[-1].append(r)
            for chunk in chunks:
                total += self._count(system, "\n\n".join(r.prompt for r in chunk), chunk[0])
            if extra := len(group) - len(chunks):
                shared = self._count(system, ".", group[0]) - self._count("", ".", _PLAIN)
                total += shared * extra
        return total
