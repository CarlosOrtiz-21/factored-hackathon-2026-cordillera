"""Model profiles: every agent config sends only parameters its model accepts."""

import pytest

from cardagent.config import Settings
from cardagent.llm.profiles import (
    MODEL_PROFILES,
    AgentLLMConfig,
    LLMConfigError,
    build_agent_configs,
    config_for,
    estimate_cost,
    request_params,
)


def test_default_mix_haiku_triage_sonnet_rest():
    configs = build_agent_configs(Settings(_env_file=None))
    assert configs["triage"].model == "claude-haiku-4-5"
    assert {configs[a].model for a in ("executor", "policy_rag", "critic")} == {"claude-sonnet-5"}
    # Haiku gets the original prompt's temperature; Sonnet gets effort instead.
    assert request_params(configs["triage"]) == {
        "model": "claude-haiku-4-5",
        "max_tokens": 256,
        "extra_body": {"temperature": 0.0},
    }
    assert request_params(configs["executor"]) == {
        "model": "claude-sonnet-5",
        "max_tokens": 4096,
        "output_config": {"effort": "medium"},
    }


def test_all_haiku_ablation_is_only_a_settings_change():
    haiku = "claude-haiku-4-5"
    configs = build_agent_configs(
        Settings(
            _env_file=None,
            triage_model=haiku,
            executor_model=haiku,
            rag_model=haiku,
            critic_model=haiku,
        )
    )
    assert all(c.effort is None and c.temperature is not None for c in configs.values())


@pytest.mark.parametrize("model", list(MODEL_PROFILES))
@pytest.mark.parametrize("agent", ["triage", "executor", "policy_rag", "critic"])
def test_request_params_never_include_unsupported_parameters(agent, model):
    params = request_params(config_for(agent, model))
    profile = MODEL_PROFILES[model]
    assert ("extra_body" in params) == profile.supports_temperature
    assert "temperature" not in params  # SDK 1.x: only via extra_body
    assert ("output_config" in params) == profile.supports_effort
    assert "top_p" not in params


def test_temperature_on_sonnet_fails_at_startup():
    with pytest.raises(LLMConfigError, match="temperature"):
        AgentLLMConfig(model="claude-sonnet-5", max_tokens=100, temperature=0.0)


def test_effort_on_haiku_fails_at_startup():
    with pytest.raises(LLMConfigError, match="effort"):
        AgentLLMConfig(model="claude-haiku-4-5", max_tokens=100, effort="low")


def test_unknown_model_fails_at_startup():
    with pytest.raises(LLMConfigError, match="unknown model"):
        build_agent_configs(Settings(_env_file=None, critic_model="gpt-5"))


def test_estimate_cost():
    # 10k input + 1k output on Sonnet 5: 10_000*2/1e6 + 1_000*10/1e6 = 0.03
    assert estimate_cost("claude-sonnet-5", 10_000, 1_000) == pytest.approx(0.03)
    assert estimate_cost("claude-haiku-4-5", 10_000, 1_000) == pytest.approx(0.015)
