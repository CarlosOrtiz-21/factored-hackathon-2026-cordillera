"""Graph state (docs/ARCHITECTURE.md §3).

Values are plain JSON-serializable types: the checkpointer persists state between the
HITL interrupt and the approval, so it must survive a round-trip to disk.

`Annotated[list, operator.add]` tells LangGraph to APPEND a node's returned list instead
of replacing the field: that is what makes `audit_trail` append-only and keeps the
conversation history. Everything else is per-turn and reset by `turn_input()`.
"""

import operator
from typing import Annotated, Any, TypedDict


class GraphState(TypedDict, total=False):
    # Identity: set by the API from the authenticated session, never by an LLM.
    thread_id: str
    session_id: str
    customer_id: str
    requester_id: str
    session_expires_at: str  # ISO; tools re-check it on every call

    # Accumulating across turns
    messages: Annotated[list[dict[str, str]], operator.add]  # sanitized conversation
    audit_trail: Annotated[list[dict[str, Any]], operator.add]  # tokens only
    usage: Annotated[list[dict[str, Any]], operator.add]
    react_steps: Annotated[list[dict[str, Any]], operator.add]

    # Per turn: input and triage
    turn_id: str
    sanitized_input: str
    pii_token_refs: list[str]
    language: str
    intent: str | None
    urgency: str
    injection_suspected: bool
    triage_source: str

    # Per turn: executor (ReAct)
    executor_messages: list[dict[str, Any]] | None  # native Claude messages for this turn
    pending_tool_calls: list[dict[str, Any]]
    react_iterations: int
    tool_failures: int
    read_results: list[dict[str, Any]]
    proposed_action: dict[str, Any] | None

    # Per turn: RAG
    rag_context: list[dict[str, Any]]
    citations: list[str]
    answerable: bool | None

    # Per turn: critique
    draft_response: str
    critic_feedback: str
    risk_score: float
    safety_violations: list[str]
    verdict: str | None
    retries: int
    requires_human_approval: bool

    # Per turn: HITL, execution, verification
    approval_request: dict[str, Any] | None
    human_decision: dict[str, Any] | None
    approval_grant: dict[str, Any] | None
    action_result: dict[str, Any] | None

    # Per turn: output
    route_reason: str | None  # why the turn ended in reject/escalate (for the audit + eval)
    escalation: dict[str, Any] | None
    interim_response: str | None  # shown while waiting for approval
    final_response: str
    suggestions: list[str]  # quick replies for the UI


def turn_input(
    *,
    thread_id: str,
    session_id: str,
    customer_id: str,
    session_expires_at: str,
    turn_id: str,
    sanitized_input: str,
    pii_token_refs: list[str],
) -> GraphState:
    """Input for one customer message: identity from the session + reset per-turn fields."""
    return GraphState(
        thread_id=thread_id,
        session_id=session_id,
        customer_id=customer_id,
        requester_id=customer_id,
        session_expires_at=session_expires_at,
        turn_id=turn_id,
        sanitized_input=sanitized_input,
        pii_token_refs=pii_token_refs,
        messages=[{"role": "user", "content": sanitized_input}],
        language="es",
        intent=None,
        urgency="medium",
        injection_suspected=False,
        triage_source="",
        executor_messages=None,
        pending_tool_calls=[],
        react_iterations=0,
        tool_failures=0,
        read_results=[],
        proposed_action=None,
        rag_context=[],
        citations=[],
        answerable=None,
        draft_response="",
        critic_feedback="",
        risk_score=0.0,
        safety_violations=[],
        verdict=None,
        retries=0,
        requires_human_approval=False,
        approval_request=None,
        human_decision=None,
        approval_grant=None,
        action_result=None,
        route_reason=None,
        escalation=None,
        interim_response=None,
        final_response="",
        suggestions=[],
    )
