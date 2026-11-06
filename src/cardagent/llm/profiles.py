"""Model profiles: which request parameters each Claude model accepts, and what it costs.

Models differ in how sampling is controlled:
- Haiku 4.5 accepts `temperature`, but rejects `output_config.effort`.
- Sonnet 5 and Opus 5 reject `temperature`/`top_p` (HTTP 400). They use `effort`, and
  run adaptive thinking by default, which consumes the `max_tokens` budget.

So each agent declares BOTH controls (its original temperature and an effort level),
and the model's profile decides which one is sent. Swapping a model (e.g. the all-Haiku
ablation) is then only a `.env` change. An invalid combination fails at startup, not
with a 400 in the middle of a customer conversation.

Prices: USD per 1M tokens, Anthropic first-party API (claude-api reference, cached
2026-06-24). They feed the cost-per-case metric; re-check before quoting externally.
"""

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

from cardagent.config import Settings, get_settings

Effort = Literal["low", "medium", "high"]
AgentName = Literal["triage", "executor", "policy_rag", "critic"]


@dataclass(frozen=True)
class ModelProfile:
    supports_temperature: bool
    supports_effort: bool
    thinks_by_default: bool  # adaptive thinking on unless disabled; needs a bigger budget
    input_usd_per_mtok: float
    output_usd_per_mtok: float


MODEL_PROFILES: dict[str, ModelProfile] = {
    "claude-haiku-4-5": ModelProfile(True, False, False, 1.0, 5.0),
    "claude-sonnet-5": ModelProfile(False, True, True, 2.0, 10.0),
    "claude-opus-5": ModelProfile(False, True, True, 5.0, 25.0),
}


@dataclass(frozen=True)
class AgentSpec:
    """What an agent needs, independent of the model that serves it."""

    temperature: float  # from docs/PROJECT_PROMPT.md; used when the model supports it
    effort: Effort  # used when the model supports effort
    max_tokens: int  # models without default thinking
    max_tokens_thinking: int  # models whose adaptive thinking shares the budget


AGENT_SPECS: dict[AgentName, AgentSpec] = {
    "triage": AgentSpec(temperature=0.0, effort="low", max_tokens=256, max_tokens_thinking=2048),
    "executor": AgentSpec(0.1, "medium", 1024, 4096),
    "policy_rag": AgentSpec(0.2, "medium", 1536, 4096),
    "critic": AgentSpec(0.0, "low", 512, 2048),
}


class LLMConfigError(ValueError):
    pass


@dataclass(frozen=True)
class AgentLLMConfig:
    model: str
    max_tokens: int
    temperature: float | None = None
    effort: Effort | None = None

    def __post_init__(self) -> None:
        profile = MODEL_PROFILES.get(self.model)
        if profile is None:
            raise LLMConfigError(f"unknown model '{self.model}': add it to MODEL_PROFILES")
        if self.temperature is not None and not profile.supports_temperature:
            raise LLMConfigError(f"{self.model} rejects `temperature`; use `effort`")
        if self.effort is not None and not profile.supports_effort:
            raise LLMConfigError(f"{self.model} rejects `effort`; use `temperature`")


def config_for(agent: AgentName, model: str) -> AgentLLMConfig:
    """The agent's needs, translated into the parameters this model accepts."""
    spec = AGENT_SPECS[agent]
    profile = MODEL_PROFILES.get(model)
    if profile is None:
        raise LLMConfigError(f"unknown model '{model}': add it to MODEL_PROFILES")
    return AgentLLMConfig(
        model=model,
        max_tokens=spec.max_tokens_thinking if profile.thinks_by_default else spec.max_tokens,
        temperature=spec.temperature if profile.supports_temperature else None,
        effort=spec.effort if profile.supports_effort else None,
    )


def build_agent_configs(settings: Settings) -> dict[AgentName, AgentLLMConfig]:
    return {
        "triage": config_for("triage", settings.triage_model),
        "executor": config_for("executor", settings.executor_model),
        "policy_rag": config_for("policy_rag", settings.rag_model),
        "critic": config_for("critic", settings.critic_model),
    }


@lru_cache
def get_agent_configs() -> dict[AgentName, AgentLLMConfig]:
    return build_agent_configs(get_settings())


def request_params(config: AgentLLMConfig) -> dict[str, Any]:
    """kwargs for `client.messages.create/parse`, with only parameters the model accepts."""
    params: dict[str, Any] = {"model": config.model, "max_tokens": config.max_tokens}
    if config.temperature is not None:
        # anthropic SDK 1.x removed `temperature` as a typed argument (newer models reject
        # it). For models that still accept it, the documented path is `extra_body`.
        params["extra_body"] = {"temperature": config.temperature}
    if config.effort is not None:
        params["output_config"] = {"effort": config.effort}
    return params


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """USD for one call, from the response's `usage` (thinking tokens bill as output)."""
    p = MODEL_PROFILES[model]
    return (input_tokens * p.input_usd_per_mtok + output_tokens * p.output_usd_per_mtok) / 1e6
