"""Records produced by deterministic parts of the system (HITL, tools, audit, handoff)."""

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from cardagent.schemas.enums import Decision, Intent, Language


def utc_now() -> datetime:
    return datetime.now(UTC)


class Record(BaseModel):
    # Records are facts once written: frozen so nothing mutates them afterwards.
    model_config = ConfigDict(extra="forbid", frozen=True)


class HumanDecision(Record):
    decision: Decision
    approver_id: str
    comment: str = ""
    ts: datetime = Field(default_factory=utc_now)


class ActionResult(Record):
    executed: bool
    verified: bool  # True only if a re-read of the resource confirmed the expected state
    observed_state: dict[str, Any] = Field(default_factory=dict)


class AuditEvent(Record):
    """One append-only audit entry. Payloads carry PII tokens only, never raw values."""

    node: str
    event: str
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: datetime = Field(default_factory=utc_now)


class UsageRecord(Record):
    node: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0


class VerifiedFact(Record):
    """One fact a tool read, tied to the call that read it. `call_id` is the same id the
    `tool_called` audit event carries, so an advisor can trace the fact to its evidence."""

    fact: str  # tokens and last-4 digits only
    tool: str
    call_id: str  # "<turn_id>/<tool_use id>"
    ts: str


class HandoffPayload(Record):
    """What a human agent receives on escalation: facts, not a raw transcript dump.

    Verified facts come from tool calls and point at them; the customer's own words are
    kept apart in `customer_statements`, never mixed with the facts; `priority` is set by
    code (`urgent` when a card may still be exposed because a block couldn't be completed).
    """

    case_id: str = ""  # shown to the customer so they can refer to it
    reason: str
    customer_request: str  # sanitized
    intent: Intent | None
    language: Language
    verified_facts: list[VerifiedFact] = Field(default_factory=list)
    actions_taken: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    customer_statements: list[str] = Field(default_factory=list)  # sanitized, last few
    priority: Literal["normal", "urgent"] = "normal"
