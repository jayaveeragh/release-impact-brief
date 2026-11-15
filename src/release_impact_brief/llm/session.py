"""Runs LLM requests and keeps track of what they cost.

With cost control on, a run first counts its input tokens (the API's own counter)
and prices a range: at least the input, at most the input plus every request's
max_tokens of output (thinking counts as output, so the worst case is far above
the usual cost). It refuses to start only if the input alone would pass the cap,
and during the run it stops once actual spend passes it. With cost control off
there is no counting and no cap. Actual cost is added up from each response's
usage either way, since that needs no extra call.
"""

import threading
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from release_impact_brief.llm.base import LlmError, LlmRequest, LlmResponse, Provider, Usage
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
    min_usd: float | None  # the input alone; None if the model has no known price
    max_usd: float | None  # input plus every request's max_tokens of output


class LlmSession:
    def __init__(self, provider: Provider, settings: Settings) -> None:
        self.provider = provider
        self.settings = settings
        self.spent_usd = 0.0
        self.unpriced_calls = 0  # responses from a model without a known price
        self._lock = threading.Lock()

    def estimate(self, requests: Iterable[LlmRequest]) -> Estimate:
        requests = list(requests)
        input_tokens = self.provider.count_tokens(requests) if requests else 0
        max_output = sum(r.max_tokens for r in requests)
        least = cost_usd(self.provider.model, Usage(input_tokens, 0))
        worst = cost_usd(self.provider.model, Usage(input_tokens, max_output))
        return Estimate(len(requests), input_tokens, max_output, least, worst)

    def _cap_message(self, what: str) -> str:
        return (f"{what} passes the spending cap of ${self.settings.spend_cap_usd:.4f}; "
                "raise RIB_SPEND_CAP_USD or set RIB_COST_CONTROL=off")  # fmt: skip

    def check_cap(self, requests: list[LlmRequest]) -> None:
        """Refuse a run whose input alone would pass the cap (cost control on only)."""
        if not self.settings.cost_control or not requests:
            return
        estimate = self.estimate(requests)
        if estimate.min_usd is None:
            raise CostUnknown(
                f"no price known for {self.provider.model}, so the cap can't be enforced; "
                "add it to PRICES or set RIB_COST_CONTROL=off"
            )
        if self.spent_usd + estimate.min_usd > self.settings.spend_cap_usd:
            raise SpendingCapExceeded(
                self._cap_message(
                    f"input alone (${estimate.min_usd:.4f} for {len(requests)} requests)"
                )
            )

    def _one(self, request: LlmRequest) -> LlmResponse:
        with self._lock:
            if self.settings.cost_control and self.spent_usd > self.settings.spend_cap_usd:
                raise SpendingCapExceeded(self._cap_message(f"spent ${self.spent_usd:.4f}"))
        response = self.provider.complete(request)
        cost = cost_usd(response.model, response.usage)
        with self._lock:
            if cost is None:
                self.unpriced_calls += 1
            else:
                self.spent_usd += cost
        return response

    def run_all(self, requests: Iterable[LlmRequest]) -> list[LlmResponse | LlmError]:
        """Run every request, `concurrency` at a time, in order; a request that fails with
        an LlmError (refusal, truncation) is returned in its place instead of stopping
        the others. Passing the spending cap stops the run. Requests already in flight
        when the cap is passed still finish, so a run can overshoot by at most
        `concurrency` requests."""
        requests = list(requests)
        self.check_cap(requests)

        def attempt(request: LlmRequest) -> LlmResponse | LlmError:
            try:
                return self._one(request)
            except LlmError as error:
                return error

        if self.settings.concurrency == 1:
            return [attempt(r) for r in requests]
        with ThreadPoolExecutor(max_workers=self.settings.concurrency) as pool:
            return list(pool.map(attempt, requests))

    def run(self, requests: Iterable[LlmRequest]) -> list[LlmResponse]:
        """Like run_all, but the first failed request raises."""
        results = self.run_all(requests)
        for result in results:
            if isinstance(result, LlmError):
                raise result
        return [r for r in results if isinstance(r, LlmResponse)]
