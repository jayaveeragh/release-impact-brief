"""Open models running locally through Ollama (https://ollama.com): free, private, slower.

The JSON schema goes in Ollama's `format`, which constrains the answer to it.
Thinking is switched off (`think: false`) for speed, and the context window is
set explicitly because Ollama's default is too small for a module or repo
prompt. Ollama has no token-counting endpoint, so estimates use ~4 characters
per token; local models cost nothing, so the estimate only sizes the work.
"""

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

from release_impact_brief.llm.base import (
    LlmError,
    LlmRequest,
    LlmResponse,
    LlmTruncated,
    LlmUnavailable,
    Usage,
)

_TIMEOUT = 900.0  # seconds; a long prompt on a laptop, behind queued requests, is slow

Post = Callable[[str, dict[str, Any]], dict[str, Any]]


def _post(url: str, body: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(  # noqa: S310 - scheme checked in settings (http/https)
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:  # noqa: S310
        return dict(json.loads(response.read()))


class OllamaProvider:
    def __init__(self, name: str, base_url: str = "http://localhost:11434",
                 context: int = 32768, post: Post = _post) -> None:  # fmt: skip
        self.name = name  # Ollama's model name, e.g. qwen3:14b
        self.model = f"ollama:{name}"
        self.base_url = base_url.rstrip("/")
        self.context = context
        self._post = post

    def complete(self, request: LlmRequest) -> LlmResponse:
        body: dict[str, Any] = {
            "model": self.name,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.prompt},
            ],  # fmt: skip
            "stream": False,
            "think": False,
            "options": {
                "num_ctx": self.context,
                "num_predict": request.max_tokens,
                "temperature": 0,
            },  # fmt: skip
        }
        if request.schema is not None:
            body["format"] = request.schema
        try:
            answer = self._post(f"{self.base_url}/api/chat", body)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise LlmUnavailable(
                    f"Ollama doesn't have {self.name}: run `ollama pull {self.name}`"
                ) from None
            raise LlmError(f"Ollama answered HTTP {error.code}") from None
        except (urllib.error.URLError, ConnectionError, TimeoutError) as error:
            raise LlmUnavailable(
                f"can't reach Ollama at {self.base_url} ({error}): start it with `ollama serve`"
            ) from None
        if answer.get("done_reason") == "length":
            raise LlmTruncated(f"reply hit max_tokens={request.max_tokens}")
        usage = Usage(int(answer.get("prompt_eval_count", 0)), int(answer.get("eval_count", 0)))
        return LlmResponse(str(answer["message"]["content"]), usage, self.model)

    def count_tokens(self, requests: Sequence[LlmRequest]) -> int:
        return sum(len(r.system) + len(r.prompt) for r in requests) // 4 + len(requests)
