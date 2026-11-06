"""Tool registry: the allowlist between an LLM's tool call and the bank.

`call()` is the only way to run a tool. In order, it:
1. rejects names that aren't registered (an LLM can ask for anything);
2. validates arguments against the tool's Pydantic model (extra fields rejected);
3. re-checks that the session hasn't expired mid-conversation;
4. requires an ApprovalGrant for write tools;
5. optionally injects faults, for failure testing in the evaluation.

`read_only()` gives the executor agent a registry where write tools don't exist at all.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from cardagent.bank import tools
from cardagent.bank.errors import (
    ApprovalRequiredError,
    BankError,
    SessionExpiredError,
    ToolArgsError,
    UnregisteredToolError,
)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: Literal["read", "write"]
    description: str
    args_model: type[BaseModel]
    fn: Callable


@dataclass(frozen=True)
class ToolResult:
    tool: str
    ok: bool
    data: dict[str, Any] | None = None
    error_code: str | None = None
    error: str | None = None


DEFAULT_TOOLS = (
    ToolSpec(
        "list_cards",
        "read",
        "List the customer's cards (tokens, kind, last 4, status).",
        tools.NoArgs,
        tools.list_cards,
    ),
    ToolSpec(
        "get_card_status",
        "read",
        "Status, limit, balance, and expiration of one of the customer's cards.",
        tools.CardArgs,
        tools.get_card_status,
    ),
    ToolSpec(
        "get_card_transactions",
        "read",
        "Recent transactions of one of the customer's cards.",
        tools.TransactionsArgs,
        tools.get_card_transactions,
    ),
    ToolSpec(
        "block_card",
        "write",
        "Block a card. Requires a human approval bound to the idempotency key.",
        tools.BlockCardArgs,
        tools.block_card,
    ),
)


@dataclass
class ToolRegistry:
    specs: dict[str, ToolSpec] = field(default_factory=lambda: {s.name: s for s in DEFAULT_TOOLS})
    fault_injector: Callable[[str], None] | None = None  # raises to simulate an outage

    def read_only(self) -> "ToolRegistry":
        return ToolRegistry(
            {n: s for n, s in self.specs.items() if s.kind == "read"}, self.fault_injector
        )

    def is_write(self, name: str) -> bool:
        spec = self.specs.get(name)
        return spec is not None and spec.kind == "write"

    def definitions(self) -> list[dict]:
        """Tool definitions for an LLM's native tool calling, derived from Pydantic."""
        return [
            {
                "name": s.name,
                "description": s.description,
                "input_schema": s.args_model.model_json_schema(),
            }
            for s in self.specs.values()
        ]

    def call(
        self,
        name: str,
        args: dict,
        ctx: tools.ToolContext,
        approval: tools.ApprovalGrant | None = None,
    ) -> dict:
        spec = self.specs.get(name)
        if spec is None:
            raise UnregisteredToolError(f"tool '{name}' is not registered")
        try:
            parsed = spec.args_model.model_validate(args)
        except ValidationError as e:
            fields = ", ".join(".".join(map(str, err["loc"])) or "args" for err in e.errors())
            raise ToolArgsError(f"invalid arguments for {name}: {fields}") from None
        if ctx.clock() >= ctx.session.expires_at:
            raise SessionExpiredError("session expired")
        if spec.kind == "write" and approval is None:
            raise ApprovalRequiredError(f"'{name}' requires a human approval")
        if self.fault_injector is not None:
            self.fault_injector(name)
        if spec.kind == "write":
            return spec.fn(ctx, parsed, approval)
        return spec.fn(ctx, parsed)

    def invoke(
        self,
        name: str,
        args: dict,
        ctx: tools.ToolContext,
        approval: tools.ApprovalGrant | None = None,
    ) -> ToolResult:
        """Like call(), but returns a structured error instead of raising.

        This is what agents see: a stable error code they can react to (retry, ask the
        customer, escalate), never a stack trace.
        """
        try:
            return ToolResult(name, True, data=self.call(name, args, ctx, approval))
        except BankError as e:
            return ToolResult(name, False, error_code=e.code, error=str(e))
