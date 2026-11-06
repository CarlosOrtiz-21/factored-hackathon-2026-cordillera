"""What a system did on one case, in a shape shared by the system and the baseline.

The checks (eval/checks.py) only read Traces, so both systems are scored by the same code.
"""

from typing import Any

from pydantic import BaseModel, Field

HUMAN_APPROVER = "auditor-eval"  # the simulated human auditor of the evaluation


class Write(BaseModel):
    """One row the case added to card_actions (a write that really happened in the bank)."""

    last4: str
    customer_id: str
    approver_id: str
    status_before: str
    status_after: str


class Trace(BaseModel):
    system: str  # "system" | "baseline"
    case_id: str
    customer_id: str
    segment: str
    country: str
    outcome: str  # an eval.cases.Outcome, or "error"
    final_text: str = ""
    intent: str | None = None
    route: str | None = None
    approval_requested: bool = False
    approval_decision: str | None = None
    writes: list[Write] = Field(default_factory=list)
    verified: bool = False
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)  # {tool, ok, error}
    tool_outputs: str = ""  # JSON of successful read outputs (tokens only), for leak checks
    usage: list[dict[str, Any]] = Field(default_factory=list)
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    error: str | None = None
    citations: list[str] = Field(default_factory=list)
