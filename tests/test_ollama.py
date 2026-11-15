import json
import urllib.error
from typing import Any

import pytest

from release_impact_brief.llm import (
    LlmRequest,
    LlmSession,
    LlmTruncated,
    LlmUnavailable,
    OllamaProvider,
    Usage,
    cost_usd,
    load_settings,
    make_provider,
)

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"],
          "additionalProperties": False}  # fmt: skip
REQ = LlmRequest("sys", "prompt", 500, SCHEMA)


class _Server:
    """Stands in for Ollama's HTTP API."""

    def __init__(self, reply: dict[str, Any] | Exception) -> None:
        self.reply = reply
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        self.sent.append((url, body))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _answer(done_reason: str = "stop") -> dict[str, Any]:
    return {"model": "qwen3:14b", "message": {"role": "assistant", "content": '{"ok": true}'},
            "done": True, "done_reason": done_reason, "prompt_eval_count": 42,
            "eval_count": 7}  # fmt: skip


def test_request_shape() -> None:
    server = _Server(_answer())
    provider = OllamaProvider("qwen3:14b", context=32768, post=server)
    response = provider.complete(REQ)
    url, body = server.sent[0]
    assert url == "http://localhost:11434/api/chat"
    assert body == {
        "model": "qwen3:14b",
        "messages": [{"role": "system", "content": "sys"}, {"role": "user", "content": "prompt"}],
        "format": SCHEMA,
        "stream": False,
        "think": False,
        "options": {"num_ctx": 32768, "num_predict": 500, "temperature": 0},
    }
    assert response.text == '{"ok": true}'
    assert response.usage == Usage(42, 7)
    assert response.model == "ollama:qwen3:14b"


def test_plain_text_request_has_no_format() -> None:
    server = _Server(_answer())
    OllamaProvider("qwen3:14b", post=server).complete(LlmRequest("s", "p", 10))
    assert "format" not in server.sent[0][1]


def test_cut_off_answer_raises() -> None:
    with pytest.raises(LlmTruncated):
        OllamaProvider("qwen3:14b", post=_Server(_answer("length"))).complete(REQ)


def test_server_not_running_says_how_to_start_it() -> None:
    down = _Server(urllib.error.URLError(ConnectionRefusedError(61, "refused")))
    with pytest.raises(LlmUnavailable, match="ollama serve"):
        OllamaProvider("qwen3:14b", post=down).complete(REQ)


def test_missing_model_says_how_to_pull_it() -> None:
    missing = urllib.error.HTTPError("u", 404, "Not Found", {}, None)  # type: ignore[arg-type]
    with pytest.raises(LlmUnavailable, match="ollama pull qwen3:14b"):
        OllamaProvider("qwen3:14b", post=_Server(missing)).complete(REQ)


def test_unavailable_stops_a_run_instead_of_failing_each_request() -> None:
    down = _Server(urllib.error.URLError(ConnectionRefusedError(61, "refused")))
    settings = load_settings({"RIB_MODEL": "ollama:qwen3:14b"}, env_file=None)
    with pytest.raises(LlmUnavailable):
        LlmSession(OllamaProvider("qwen3:14b", post=down), settings).run_all([REQ, REQ])
    assert len(down.sent) == 1


def test_local_models_cost_nothing_and_need_no_counting_call() -> None:
    assert cost_usd("ollama:qwen3:14b", Usage(1_000_000, 1_000_000)) == 0
    provider = OllamaProvider("qwen3:14b", post=_Server(_answer()))
    assert provider.count_tokens([REQ, REQ]) > 0  # a local estimate, no server call
    settings = load_settings({"RIB_MODEL": "ollama:qwen3:14b"}, env_file=None)
    session = LlmSession(provider, settings)
    assert session.estimate([REQ]).max_usd == 0


def test_settings() -> None:
    s = load_settings({}, env_file=None)
    assert s.model == "ollama:qwen3:14b"  # free by default; Claude is opt-in
    env = {"RIB_OLLAMA_URL": "http://127.0.0.1:9999", "RIB_OLLAMA_CONTEXT": "8192"}
    provider = make_provider(load_settings(env, env_file=None))
    assert isinstance(provider, OllamaProvider)
    assert (provider.base_url, provider.context, provider.name) == (
        "http://127.0.0.1:9999", 8192, "qwen3:14b")  # fmt: skip
    with pytest.raises(ValueError, match="RIB_OLLAMA_URL"):
        load_settings({"RIB_OLLAMA_URL": "file:///etc/passwd"}, env_file=None)
    with pytest.raises(ValueError, match="RIB_OLLAMA_CONTEXT"):
        load_settings({"RIB_OLLAMA_CONTEXT": "lots"}, env_file=None)


def test_real_post_sends_json(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class _Response:
        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def read(self) -> bytes:
            return json.dumps(_answer()).encode()

    def fake_urlopen(request: Any, timeout: float) -> _Response:
        seen.update(url=request.full_url, data=json.loads(request.data), timeout=timeout)
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    OllamaProvider("qwen3:14b").complete(REQ)
    assert seen["url"] == "http://localhost:11434/api/chat"
    assert seen["data"]["model"] == "qwen3:14b"
    assert seen["timeout"] >= 600  # local models are slow; a summary can take minutes
