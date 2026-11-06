"""LLM client: every call to Claude goes through here.

Resilience chain for structured output (docs/ARCHITECTURE.md §5):
    structured output (output_config.format) -> json.loads -> json_repair -> Pydantic
    -> on any failure: value=None + error, and the calling node takes its safe path.
Nothing here raises into the graph: API errors, refusals, truncation, and invalid JSON
all become `error`. Retries on 429/5xx/timeouts are bounded by the SDK (max_retries).

`FakeLLM` scripts responses for tests, so unit tests never call the API.
"""

import json
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

import anthropic
from json_repair import repair_json
from pydantic import BaseModel, ValidationError

from cardagent.config import get_settings
from cardagent.llm.profiles import AgentName, estimate_cost, get_agent_configs, request_params
from cardagent.schemas.records import UsageRecord

T = TypeVar("T", bound=BaseModel)


@dataclass
class StructuredResult:
    value: BaseModel | None
    usage: UsageRecord
    error: str | None = None
    repaired: bool = False  # the JSON needed json_repair: logged, counted in the eval


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolTurn:
    text: str  # visible text the model wrote before/around its tool calls
    tool_calls: list[ToolCall]
    assistant_content: list[dict]  # the full assistant message, to send back next turn
    stop_reason: str | None
    usage: UsageRecord
    error: str | None = None


class LLM(Protocol):
    def structured(
        self, agent: AgentName, system: str, messages: list[dict], schema: type[T]
    ) -> StructuredResult: ...

    def with_tools(
        self, agent: AgentName, system: str, messages: list[dict], tools: list[dict]
    ) -> ToolTurn: ...


def validate_json(text: str, schema: type[T]) -> tuple[T | None, bool, str | None]:
    """json.loads -> json_repair -> Pydantic. Returns (value, repaired, error)."""
    repaired = False
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        repaired = True
        try:
            data = json.loads(repair_json(text))
        except (json.JSONDecodeError, ValueError) as e:
            return None, repaired, f"unparseable_json: {e}"
    try:
        return schema.model_validate(data), repaired, None
    except ValidationError as e:
        return None, repaired, f"schema_violation: {e.error_count()} errors"


class AnthropicLLM:
    def __init__(
        self,
        client: anthropic.Anthropic | None = None,
        timeout_s: float = 45.0,
        max_retries: int = 2,
    ):
        if client is None:
            # .env is read by pydantic-settings, not exported to os.environ, so pass the key
            # explicitly. Without one, the SDK falls back to env vars / `ant auth login`.
            settings = get_settings()
            key = settings.anthropic_api_key
            headers = (
                {"anthropic-workspace-id": settings.anthropic_workspace_id}
                if settings.anthropic_workspace_id
                else None
            )
            client = anthropic.Anthropic(
                api_key=key.get_secret_value() if key else None,
                default_headers=headers,
                timeout=timeout_s,
                max_retries=max_retries,
            )
        self._client = client

    def _usage(self, agent: str, model: str, response, started: float) -> UsageRecord:
        u = getattr(response, "usage", None)
        return UsageRecord(
            node=agent,
            model=model,
            input_tokens=getattr(u, "input_tokens", 0) or 0,
            output_tokens=getattr(u, "output_tokens", 0) or 0,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
        )

    def _call(self, agent: AgentName, **kwargs):
        config = get_agent_configs()[agent]
        params = request_params(config)
        if "output_config" in kwargs:  # effort and format share output_config
            params["output_config"] = {
                **params.get("output_config", {}),
                **kwargs.pop("output_config"),
            }
        started = time.perf_counter()
        try:
            response = self._client.messages.create(**params, **kwargs)
        except anthropic.APIError as e:
            usage = UsageRecord(
                node=agent,
                model=config.model,
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
            )
            return None, usage, f"api_error: {type(e).__name__}"
        usage = self._usage(agent, config.model, response, started)
        if response.stop_reason == "refusal":
            return None, usage, "refusal"
        if response.stop_reason == "max_tokens":
            return response, usage, "truncated"
        return response, usage, None

    def structured(self, agent, system, messages, schema):
        response, usage, error = self._call(
            agent,
            system=system,
            messages=messages,
            output_config={
                "format": {"type": "json_schema", "schema": anthropic.transform_schema(schema)}
            },
        )
        if response is None or error == "truncated":
            return StructuredResult(None, usage, error)
        text = "".join(b.text for b in response.content if b.type == "text")
        value, repaired, error = validate_json(text, schema)
        return StructuredResult(value, usage, error, repaired)

    def with_tools(self, agent, system, messages, tools):
        response, usage, error = self._call(agent, system=system, messages=messages, tools=tools)
        if response is None:
            return ToolTurn("", [], [], None, usage, error)
        calls = [
            ToolCall(b.id, b.name, dict(b.input)) for b in response.content if b.type == "tool_use"
        ]
        text = "".join(b.text for b in response.content if b.type == "text")
        # Send every block back unchanged (incl. thinking blocks) on the next turn.
        content = [b.model_dump(exclude_none=True) for b in response.content]
        return ToolTurn(text, calls, content, response.stop_reason, usage, error)


def usage_cost(usage: list[UsageRecord]) -> float:
    return sum(estimate_cost(u.model, u.input_tokens, u.output_tokens) for u in usage)


# --- test double ------------------------------------------------------------------


@dataclass
class FakeLLM:
    """Scripted LLM. Queue per agent:
    - structured: a dict (validated against the schema), a raw str, or None (failure);
    - with_tools: {"text": str, "tool_calls": [(name, input), ...]} or None (failure).
    Every call is recorded in `calls` so tests can assert what each agent saw.
    """

    script: dict[str, deque] = field(default_factory=dict)
    calls: list[tuple[str, list[dict]]] = field(default_factory=list)

    def add(self, agent: str, *responses) -> "FakeLLM":
        self.script.setdefault(agent, deque()).extend(responses)
        return self

    def _next(self, agent: str, messages: list[dict]):
        self.calls.append((agent, messages))
        queue = self.script.get(agent)
        if not queue:
            raise AssertionError(f"FakeLLM: no scripted response left for '{agent}'")
        return queue.popleft()

    def _usage(self, agent: str) -> UsageRecord:
        return UsageRecord(
            node=agent,
            model=get_agent_configs()[agent].model,
            input_tokens=100,
            output_tokens=20,
            latency_ms=1.0,
        )

    def structured(self, agent, system, messages, schema):
        item = self._next(agent, messages)
        if item is None:
            return StructuredResult(None, self._usage(agent), "api_error: scripted")
        text = item if isinstance(item, str) else json.dumps(item)
        value, repaired, error = validate_json(text, schema)
        return StructuredResult(value, self._usage(agent), error, repaired)

    def with_tools(self, agent, system, messages, tools):
        item = self._next(agent, messages)
        if item is None:
            return ToolTurn("", [], [], None, self._usage(agent), "api_error: scripted")
        calls = [
            ToolCall(f"toolu_{len(self.calls)}_{i}", n, inp)
            for i, (n, inp) in enumerate(item.get("tool_calls", []))
        ]
        content = ([{"type": "text", "text": item["text"]}] if item.get("text") else []) + [
            {"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in calls
        ]
        stop = "tool_use" if calls else "end_turn"
        return ToolTurn(item.get("text", ""), calls, content, stop, self._usage(agent))
