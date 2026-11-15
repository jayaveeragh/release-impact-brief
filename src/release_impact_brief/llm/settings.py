"""LLM settings from the environment and `.env` (gitignored).

RIB_MODEL         `ollama:<model>` for a free local open model (default ollama:qwen3:14b),
                  a Claude model ID such as claude-opus-5-5 (paid), or `fake` (canned answers)
RIB_EFFORT        low | medium | high | xhigh | max (default medium)
RIB_COST_CONTROL  on | off: estimate and cap spending before each run (default on)
RIB_SPEND_CAP_USD cap per run in US dollars when cost control is on (default 1.0)
RIB_CONCURRENCY   requests sent in parallel, 1-32 (default 4)
RIB_OLLAMA_URL    where Ollama listens (default http://localhost:11434)
RIB_OLLAMA_CONTEXT context window asked of Ollama, in tokens (default 32768)
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
from release_impact_brief.llm.ollama_provider import OllamaProvider

_EFFORTS = ("low", "medium", "high", "xhigh", "max")
_ON = ("on", "true", "1", "yes")
_OFF = ("off", "false", "0", "no")


@dataclass(frozen=True)
class Settings:
    model: str
    effort: str
    cost_control: bool
    spend_cap_usd: float
    concurrency: int = 4
    ollama_url: str = "http://localhost:11434"
    ollama_context: int = 32768


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
    raw_workers = source.get("RIB_CONCURRENCY", "4")
    if not raw_workers.isdigit() or not 1 <= int(raw_workers) <= 32:
        raise ValueError(
            f"RIB_CONCURRENCY must be a whole number from 1 to 32, not {raw_workers!r}"
        )
    url = source.get("RIB_OLLAMA_URL", "http://localhost:11434")
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"RIB_OLLAMA_URL must start with http:// or https://, not {url!r}")
    raw_context = source.get("RIB_OLLAMA_CONTEXT", "32768")
    if not raw_context.isdigit() or int(raw_context) < 1024:
        raise ValueError(
            f"RIB_OLLAMA_CONTEXT must be a number of tokens >= 1024, not {raw_context!r}"
        )
    return Settings(source.get("RIB_MODEL", "ollama:qwen3:14b"), effort, control in _ON, cap,
                    int(raw_workers), url, int(raw_context))  # fmt: skip


def make_provider(settings: Settings) -> Provider:
    if settings.model == "fake":
        return FakeProvider()
    if settings.model.startswith("ollama:"):
        return OllamaProvider(settings.model.removeprefix("ollama:"), settings.ollama_url,
                              settings.ollama_context)  # fmt: skip
    return AnthropicProvider(settings.model, settings.effort)
