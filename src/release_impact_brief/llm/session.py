"""Runs LLM requests and keeps track of what they cost.

With cost control on, a run first counts its input tokens (the API's own counter)
and prices the worst case - all input plus every request's max_tokens of output -
and refuses to start if that would pass the cap; during the run it stops once
actual spend passes the cap (a fallback model can bill more than estimated). With
cost control off there is no counting and no cap. Actual cost is added up from
each response's usage either way, since that needs no extra call.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from release_impact_brief.llm.base import LlmRequest, LlmResponse, Provider, Usage
from release_impact_brief.llm.settings import Settings

# US dollars per million tokens: input, output, cache write (5 min), cache read.
# From Anthropic's price list (2026-10-06); `fake` is free. Fallback targets included.
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 5.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 2.50, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),  # prompts up to 100K tokens
    "claude-opus-5": (5.00, 25.00, 6.25, 0.50),
    "claude-opus-4-8": (5.00, 25.00, 6.25, 0.50),
    "fake": (0.0, 0.0, 0.0, 0.0),
}


def cost_usd(model: str, usage: Usage) -> float | None:
    """Cost of one response in US dollars; None if the model has no known price."""
    if model not in PRICES:
        return None
    inp, out, write, read = PRICES[model]
    return (usage.input_tokens * inp + usage.output_tokens * out
            + usage.cache_creation_input_tokens * write
            + usage.cache_read_input_tokens * read) / 1_000_000  # fmt: skip


class CostUnknown(Exception):
    pass


class SpendingCapExceeded(Exception):
    pass


@dataclass(frozen=True)
class Estimate:
    requests: int
    input_tokens: int
    max_output_tokens: int
    max_usd: float | None  # worst case; None if the model has no known price


class LlmSession:
    def __init__(self, provider: Provider, settings: Settings) -> None:
        self.provider = provider
        self.settings = settings
        self.spent_usd = 0.0
        self.unpriced_calls = 0  # responses from a model without a known price

    def estimate(self, requests: Iterable[LlmRequest]) -> Estimate:
        requests = list(requests)
        input_tokens = sum(self.provider.count_tokens(r) for r in requests)
        max_output = sum(r.max_tokens for r in requests)
        worst = cost_usd(self.provider.model, Usage(input_tokens, max_output))
        return Estimate(len(requests), input_tokens, max_output, worst)

    def _cap_message(self, what: str) -> str:
        return (f"{what} passes the spending cap of ${self.settings.spend_cap_usd:.4f}; "
                "raise RIB_SPEND_CAP_USD or set RIB_COST_CONTROL=off")  # fmt: skip

    def run(self, requests: Iterable[LlmRequest]) -> list[LlmResponse]:
        requests = list(requests)
        control = self.settings.cost_control
        cap = self.settings.spend_cap_usd
        if control:
            estimate = self.estimate(requests)
            if estimate.max_usd is None:
                raise CostUnknown(
                    f"no price known for {self.provider.model}, so the cap can't be enforced; "
                    "add it to PRICES or set RIB_COST_CONTROL=off"
                )
            if self.spent_usd + estimate.max_usd > cap:
                raise SpendingCapExceeded(
                    self._cap_message(
                        f"estimated worst case ${estimate.max_usd:.4f} for {len(requests)} requests"
                    )
                )
        responses = []
        for request in requests:
            if control and self.spent_usd > cap:
                raise SpendingCapExceeded(self._cap_message(f"spent ${self.spent_usd:.4f}"))
            response = self.provider.complete(request)
            cost = cost_usd(response.model, response.usage)
            if cost is None:
                self.unpriced_calls += 1
            else:
                self.spent_usd += cost
            responses.append(response)
        return responses
