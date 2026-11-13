from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from release_impact_brief.llm import (
    AnthropicProvider,
    CostUnknown,
    FakeProvider,
    LlmRefusal,
    LlmRequest,
    LlmSession,
    LlmTruncated,
    Settings,
    SpendingCapExceeded,
    Usage,
    cost_usd,
    load_settings,
    make_provider,
)

REQ = LlmRequest(system="You summarise code.", prompt="x" * 400, max_tokens=1000)


# ---- settings ---------------------------------------------------------------


def test_settings_defaults() -> None:
    s = load_settings({}, env_file=None)
    assert s == Settings(model="claude-opus-5-5", effort="medium", cost_control=True,
                         spend_cap_usd=1.0)  # fmt: skip


@pytest.mark.parametrize("value", ["off", "false", "0", "no", "OFF"])
def test_cost_control_can_be_switched_off(value: str) -> None:
    assert load_settings({"RIB_COST_CONTROL": value}, env_file=None).cost_control is False


@pytest.mark.parametrize(
    ("env", "message"),
    [({"RIB_COST_CONTROL": "maybe"}, "RIB_COST_CONTROL"),
     ({"RIB_SPEND_CAP_USD": "lots"}, "RIB_SPEND_CAP_USD"),
     ({"RIB_SPEND_CAP_USD": "-1"}, "RIB_SPEND_CAP_USD"),
     ({"RIB_EFFORT": "extreme"}, "RIB_EFFORT")],
)  # fmt: skip
def test_bad_settings_name_the_variable(env: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        load_settings(env, env_file=None)


def test_env_file_is_read_but_never_overrides_the_environment(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("RIB_MODEL=claude-sonnet-5-5\nRIB_SPEND_CAP_USD=3\n"
                        "ANTHROPIC_API_KEY=sk-ant-secret\n")  # fmt: skip
    s = load_settings({"RIB_SPEND_CAP_USD": "2"}, env_file=env_file)
    assert (s.model, s.spend_cap_usd) == ("claude-sonnet-5-5", 2.0)
    assert "sk-ant" not in repr(s)


def test_fake_model_needs_no_key() -> None:
    assert isinstance(make_provider(load_settings({"RIB_MODEL": "fake"}, env_file=None)),
                      FakeProvider)  # fmt: skip


# ---- prices -----------------------------------------------------------------


def test_cost_includes_cache_reads_and_writes() -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=100_000,
                  cache_creation_input_tokens=200_000, cache_read_input_tokens=500_000)  # fmt: skip
    # Opus 5.5: $4 in, $20 out, $5 cache write, $0.20 cache read per million tokens
    assert cost_usd("claude-opus-5-5", usage) == pytest.approx(4 + 2 + 1 + 0.1)
    assert cost_usd("some-new-model", usage) is None


# ---- session: cost control on -------------------------------------------------


def session(provider: FakeProvider, *, cost_control: bool = True, cap: float = 1.0,
            model: str = "claude-opus-5-5") -> LlmSession:  # fmt: skip
    return LlmSession(provider, Settings(model=model, effort="medium",
                                         cost_control=cost_control, spend_cap_usd=cap))  # fmt: skip


def test_estimate_is_exact_input_plus_worst_case_output() -> None:
    provider = FakeProvider(model="claude-opus-5-5")
    est = session(provider).estimate([REQ, REQ])
    input_tokens = provider.count_tokens(REQ) * 2
    assert (est.input_tokens, est.max_output_tokens) == (input_tokens, 2000)
    assert est.max_usd == pytest.approx((input_tokens * 4 + 2000 * 20) / 1e6)


def test_cap_stops_the_run_before_any_call() -> None:
    provider = FakeProvider(model="claude-opus-5-5")
    with pytest.raises(SpendingCapExceeded, match=r"\$0\.0010"):
        session(provider, cap=0.001).run([REQ] * 3)
    assert provider.calls == []


def test_run_under_the_cap_records_actual_spend() -> None:
    provider = FakeProvider(model="claude-opus-5-5", reply='{"ok": true}')
    s = session(provider)
    responses = s.run([REQ, REQ])
    assert [r.text for r in responses] == ['{"ok": true}'] * 2
    assert s.spent_usd == pytest.approx(sum(cost_usd(r.model, r.usage) or 0 for r in responses))
    assert s.spent_usd > 0


def test_actual_spend_crossing_the_cap_stops_the_next_call() -> None:
    # A fallback model can bill more than the estimate: simulate output above max_tokens.
    provider = FakeProvider(model="claude-opus-5-5", reply="y" * 200_000)
    s = session(provider, cap=0.5)
    with pytest.raises(SpendingCapExceeded):
        s.run([REQ] * 5)
    assert len(provider.calls) == 1


def test_unknown_price_blocks_a_capped_run() -> None:
    provider = FakeProvider(model="some-new-model")
    with pytest.raises(CostUnknown, match="RIB_COST_CONTROL=off"):
        session(provider, model="some-new-model").run([REQ])
    assert provider.calls == []


# ---- session: cost control off ------------------------------------------------


def test_cost_control_off_skips_the_estimator_and_the_cap() -> None:
    provider = FakeProvider(model="claude-opus-5-5", reply="y" * 200_000)
    s = session(provider, cost_control=False, cap=0.000001)
    s.run([REQ] * 3)
    assert provider.counted == 0  # no token counting at all
    assert len(provider.calls) == 3
    assert s.spent_usd > 0.000001  # still tracked: it costs nothing to add up


def test_cost_control_off_runs_models_without_a_price() -> None:
    provider = FakeProvider(model="some-new-model")
    s = session(provider, cost_control=False, model="some-new-model")
    s.run([REQ])
    assert s.unpriced_calls == 1


# ---- the real provider, against a stub client -------------------------------------


class _StubClient:
    def __init__(self, response: Any) -> None:
        self.created: dict[str, Any] = {}
        self.counted: dict[str, Any] = {}
        self.response = response
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self.messages = SimpleNamespace(count_tokens=self._count)

    def _create(self, **kwargs: Any) -> Any:
        self.created = kwargs
        return self.response

    def _count(self, **kwargs: Any) -> Any:
        self.counted = kwargs
        return SimpleNamespace(input_tokens=123)


def _response(stop_reason: str = "end_turn", text: str = '{"a": 1}') -> Any:
    return SimpleNamespace(
        model="claude-opus-5-5",
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category="cyber") if stop_reason == "refusal" else None,
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],  # fmt: skip
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            cache_read_input_tokens=7,
            cache_creation_input_tokens=None,
        ),  # fmt: skip
    )


def test_anthropic_provider_request_shape() -> None:
    client = _StubClient(_response())
    provider = AnthropicProvider("claude-opus-5-5", effort="medium", client=client)
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"],
              "additionalProperties": False}  # fmt: skip
    result = provider.complete(LlmRequest("sys", "prompt", 2000, schema=schema))
    sent = client.created
    assert sent["model"] == "claude-opus-5-5" and sent["max_tokens"] == 2000
    assert sent["system"] == [{"type": "text", "text": "sys",
                               "cache_control": {"type": "ephemeral"}}]  # fmt: skip
    assert sent["messages"] == [{"role": "user", "content": "prompt"}]
    expected_format = {"type": "json_schema", "schema": schema}
    assert sent["output_config"] == {"effort": "medium", "format": expected_format}
    assert (sent["fallbacks"], sent["betas"]) == ("default", ["server-side-fallback-2026-07-01"])
    assert result.text == '{"a": 1}'
    assert result.usage == Usage(10, 5, cache_read_input_tokens=7)
    assert provider.count_tokens(REQ) == 123
    assert client.counted["system"] == "You summarise code."


def test_anthropic_provider_refusal_and_truncation() -> None:
    with pytest.raises(LlmRefusal, match="cyber"):
        AnthropicProvider("m", "medium", client=_StubClient(_response("refusal"))).complete(REQ)
    with pytest.raises(LlmTruncated):
        AnthropicProvider("m", "medium", client=_StubClient(_response("max_tokens"))).complete(REQ)


# ---- live (opt-in: pytest -m llm, needs ANTHROPIC_API_KEY, costs well under a cent) ----


@pytest.mark.llm
def test_live_round_trip_with_cost_control() -> None:
    settings = load_settings()
    s = LlmSession(make_provider(settings), settings)
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"],
              "additionalProperties": False}  # fmt: skip
    request = LlmRequest("Answer in the required JSON format.", "Is 2 + 2 = 4?", 2000, schema)
    estimate = s.estimate([request])
    [response] = s.run([request])
    assert response.text.replace(" ", "") == '{"ok":true}'
    assert 0 < s.spent_usd <= (estimate.max_usd or 1) < 0.05
