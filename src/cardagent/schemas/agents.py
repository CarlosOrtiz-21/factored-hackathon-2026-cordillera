"""Structured outputs of the agents, and the arguments of the executor's intent tools.

Every model is strict (`extra="forbid"`): an LLM response with unexpected fields or
out-of-vocabulary values is rejected and routed to a safe fallback.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from cardagent.schemas.enums import Intent, Language, Urgency, Verdict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TriageOutput(StrictModel):
    intent: Intent
    urgency: Urgency
    language: Language
    injection_suspected: bool
    rationale: str = Field(description="One short sentence (max ~25 words)")

    @field_validator("rationale")
    @classmethod
    def _truncate(cls, v: str) -> str:
        # Strict where it matters (enums above), lenient where it doesn't: a long audit note
        # must not throw away a valid classification (found in the first live run).
        return v if len(v) <= 300 else v[:297] + "..."


# --- executor intent tools: the model can PROPOSE, never execute -------------------


class ProposeBlockCardArgs(StrictModel):
    """Arguments of the `propose_block_card` tool offered to the executor."""

    card_token: str = Field(pattern=r"^<TARJETA_\d+>$", description="Card token to block")
    reason: Literal["lost", "stolen", "suspicious_activity", "customer_request"]
    summary: str = Field(description="One sentence for the approver: why this card, why now")


class EscalateArgs(StrictModel):
    """Arguments of the `escalate_to_human` tool offered to the executor."""

    reason: str = Field(description="Why a human advisor must take over")
    open_questions: list[str] = Field(default_factory=list)


class ProposedAction(StrictModel):
    """A write the executor wants. Only `tool_executor` may run it, after approval."""

    tool: str
    args: dict[str, Any]  # PII tokens (e.g. "<TARJETA_1>"), never raw values
    idempotency_key: str = Field(min_length=8)
    summary: str = ""


# --- RAG and critic ----------------------------------------------------------------


class PolicyRagOutput(StrictModel):
    step_back_principle: str = Field(description="The general rule the question depends on")
    reasoning: str = Field(description="Short deduction from the cited sections to the answer")
    grounded_answer: str = Field(description="Answer for the customer, in their language")
    citations: list[str] = Field(
        default_factory=list, description="chunk_id of every section the answer relies on"
    )
    answerable: bool = Field(description="False if the documents don't support an answer")


class CriticOutput(StrictModel):
    risk_score: float = Field(ge=0.0, le=1.0)
    safety_violations: list[str] = Field(default_factory=list)
    verdict: Verdict
    feedback: str
    approved_response: str | None = None
