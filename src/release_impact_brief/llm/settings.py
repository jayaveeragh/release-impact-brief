"""LLM settings from the environment and `.env` (gitignored).

RIB_MODEL         model ID, or `fake` for the offline provider (default claude-opus-5-5)
RIB_EFFORT        low | medium | high | xhigh | max (default medium)
RIB_COST_CONTROL  on | off: estimate and cap spending before each run (default on)
RIB_SPEND_CAP_USD cap per run in US dollars when cost control is on (default 1.0)
ANTHROPIC_API_KEY read by the Anthropic SDK itself; never stored or printed here
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

from release_impact_brief.llm.anthropic_provider import AnthropicProvider
from release_impact_brief.llm.base import Provider
from release_impact_brief.llm.fake import FakeProvider

_EFFORTS = ("low", "medium", "high", "xhigh", "max")
_ON = ("on", "true", "1", "yes")
_OFF = ("off", "false", "0", "no")


@dataclass(frozen=True)
class Settings:
    model: str
    effort: str
    cost_control: bool
    spend_cap_usd: float


def load_settings(env: Mapping[str, str] | None = None,
                  env_file: Path | str | None = ".env") -> Settings:  # fmt: skip
    """Settings from `env` (default: the process environment), with `env_file` filling gaps.

    Real runs load `.env` into the process environment so the SDK finds the API key;
    variables already set always win over the file.
    """
    if env is None:
        if env_file is not None:
            load_dotenv(env_file, override=False)
        source: Mapping[str, str] = os.environ
    else:
        from_file = dotenv_values(env_file) if env_file is not None else {}
        source = {**{k: v for k, v in from_file.items() if v is not None}, **env}

    effort = source.get("RIB_EFFORT", "medium")
    if effort not in _EFFORTS:
        raise ValueError(f"RIB_EFFORT must be one of {', '.join(_EFFORTS)}, not {effort!r}")
    control = source.get("RIB_COST_CONTROL", "on").strip().lower()
    if control not in _ON + _OFF:
        raise ValueError(f"RIB_COST_CONTROL must be on or off, not {control!r}")
    raw_cap = source.get("RIB_SPEND_CAP_USD", "1.0")
    try:
        cap = float(raw_cap)
    except ValueError:
        raise ValueError(f"RIB_SPEND_CAP_USD must be a number, not {raw_cap!r}") from None
    if cap < 0:
        raise ValueError(f"RIB_SPEND_CAP_USD must not be negative, not {raw_cap!r}")
    return Settings(source.get("RIB_MODEL", "claude-opus-5-5"), effort, control in _ON, cap)


def make_provider(settings: Settings) -> Provider:
    if settings.model == "fake":
        return FakeProvider()
    return AnthropicProvider(settings.model, settings.effort)
