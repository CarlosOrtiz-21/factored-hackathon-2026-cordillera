"""The card-support graph (docs/ARCHITECTURE.md §4).

Nodes may call LLMs; ROUTING is plain Python in the `route_*` functions, so it can be
tested without a model and no model output can skip the approval step.

    triage -> executor <-> executor_tools -> critic -> human_approval -> tool_executor
           -> policy_rag ----------------------^         (interrupt)        -> verify
    critic -> respond | escalate | back to executor/policy_rag (max 2 revisions)
    reject / escalate / respond -> END

Golden rule, enforced three times:
1. the executor only has read tools plus `propose_block_card`, which executes nothing;
2. `requires_human_approval` is computed in code (write OR risk > threshold);
3. `block_card` itself refuses to run without a valid ApprovalGrant.
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, ValidationError

from cardagent.agents import prompts
from cardagent.agents.guards import (
    IntentFallback,
    detect_injection,
    guess_language,
    mask_tokens,
    raw_pii_found,
)
from cardagent.agents.templates import POLICY_SOURCES, render, suggestions
from cardagent.bank.db import BankDB
from cardagent.bank.registry import ToolRegistry, ToolResult
from cardagent.bank.sessions import Session, utc_now
from cardagent.bank.tools import ApprovalGrant, ToolContext
from cardagent.config import Settings
from cardagent.llm.client import LLM
from cardagent.pii.vault import TokenVault
from cardagent.rag.retriever import HybridRetriever
from cardagent.schemas.agents import (
    CriticOutput,
    EscalateArgs,
    PolicyRagOutput,
    ProposeBlockCardArgs,
    TriageOutput,
)
from cardagent.schemas.enums import Decision, Intent
from cardagent.schemas.records import HandoffPayload
from cardagent.schemas.state import GraphState

EXECUTOR_INTENTS = {Intent.CARD_STATUS, Intent.CARD_TRANSACTIONS, Intent.CARD_BLOCK}
HARD_VIOLATIONS = ("raw_pii", "unknown_action", "action_target_not_evidenced")
CARD_KIND_LABEL = {"credit": "Crédito", "debit": "Débito"}  # same word in es and pt

INTENT_TOOLS = [
    {
        "name": "propose_block_card",
        "description": "Propose blocking one of the customer's cards. This does NOT block: a "
        "supervisor must approve it first. Use the card token from list_cards.",
        "input_schema": ProposeBlockCardArgs.model_json_schema(),
    },
    {
        "name": "escalate_to_human",
        "description": "Hand the case to a human advisor (disputes, unsupported requests, "
        "repeated tool failures). Include what you verified and what is still open.",
        "input_schema": EscalateArgs.model_json_schema(),
    },
]


@dataclass
class Deps:
    llm: LLM
    registry: ToolRegistry
    db: BankDB
    vault: TokenVault
    settings: Settings
    intent_fallback: IntentFallback
    retriever_factory: Callable[[], HybridRetriever]
    clock: Callable[[], datetime] = utc_now
    _retriever: HybridRetriever | None = field(default=None, init=False, repr=False)

    @property
    def retriever(self) -> HybridRetriever:
        if self._retriever is None:  # loads e5 lazily, only when a policy question arrives
            self._retriever = self.retriever_factory()
        return self._retriever


class ApprovalInput(BaseModel):
    """What the approver sends to resume the graph (POST /api/approve in step 4)."""

    decision: Literal["approved", "rejected"]
    approver_id: str
    approver_role: str
    comment: str = ""


def event(deps: Deps, node: str, name: str, **payload: Any) -> dict:
    """One audit entry. Payloads hold tokens and ids only, never raw personal data."""
    return {"ts": deps.clock().isoformat(), "node": node, "event": name, "payload": payload}


def tool_context(state: GraphState, deps: Deps) -> ToolContext:
    session = Session(
        state["session_id"],
        state["customer_id"],
        datetime.fromisoformat(state["session_expires_at"]),
    )
    return ToolContext(
        session, state["thread_id"], deps.db, deps.vault, deps.settings.bank_as_of_date, deps.clock
    )


def history(state: GraphState, limit: int = 6) -> str:
    turns = state.get("messages", [])[-limit:-1]  # the last item is the current message
    return "\n".join(f"{m['role']}: {m['content']}" for m in turns) or "(no earlier turns)"


def build_graph(deps: Deps, checkpointer=None):
    s = deps.settings
    P = prompts.get(s.prompt_version)  # versioned prompt set (v1.0 / v1.1)
    read_only = deps.registry.read_only()

    def read(tool: str, args: dict, ctx: ToolContext) -> tuple[ToolResult, bool]:
        """A READ tool call with one deterministic retry on a transient outage.

        "Tool failure -> 1 retry -> escalate" (ARCHITECTURE.md §4) is a rule, so it lives
        in code: found in the live eval, the model escalated after the first
        `tool_unavailable` instead of retrying. Only reads are retried here; a failed write
        goes to verify/escalate, never to an automatic second attempt.
        Returns (result, retried).
        """
        r = read_only.invoke(tool, args, ctx)
        if r.ok or r.error_code != "tool_unavailable":
            return r, False
        return read_only.invoke(tool, args, ctx), True

    # --- triage ---------------------------------------------------------------------

    def triage(state: GraphState) -> dict:
        text = state["sanitized_input"]
        heuristic = detect_injection(text)
        messages = [
            {
                "role": "user",
                "content": f"<earlier_turns>\n{history(state)}\n</earlier_turns>\n"
                f"Latest message: {text}",
            }
        ]
        res = deps.llm.structured("triage", P.triage, messages, TriageOutput)
        out: dict[str, Any] = {"usage": [res.usage.model_dump(mode="json")]}
        if res.value is not None:
            v = res.value
            out.update(
                intent=v.intent.value,
                urgency=v.urgency.value,
                language=v.language.value,
                injection_suspected=v.injection_suspected or heuristic,
                triage_source="llm",
            )
        else:  # LLM failed: the learned classifier from step 1c takes over
            pred = deps.intent_fallback(text)
            out.update(
                intent=pred.intent if pred else None,
                urgency="medium",
                language=guess_language(text),
                injection_suspected=heuristic,
                triage_source="classifier_fallback",
            )
        out["audit_trail"] = [
            event(
                deps,
                "triage",
                "classified",
                intent=out["intent"],
                source=out["triage_source"],
                injection_llm=bool(res.value and res.value.injection_suspected),
                injection_heuristic=heuristic,
                language=out["language"],
                llm_error=res.error,
                prompt_version=P.version,
            )
        ]
        return out

    def route_triage(state: GraphState) -> str:
        if state.get("injection_suspected"):
            return "reject"
        intent = state.get("intent")
        if intent is None:
            return "escalate"
        if intent == Intent.OUT_OF_SCOPE:
            return "reject"
        if intent == Intent.HUMAN_REQUEST:
            return "escalate"
        if intent == Intent.CARD_POLICY:
            return "policy_rag"
        return "executor"

    # --- executor (explicit ReAct: reason node <-> tools node) -----------------------

    tools = read_only.definitions() + INTENT_TOOLS

    def executor(state: GraphState) -> dict:
        if state["react_iterations"] >= s.max_react_iterations:
            return {
                "route_reason": "react_limit",
                "audit_trail": [event(deps, "executor", "react_limit_reached")],
            }
        messages = state.get("executor_messages") or [
            {
                "role": "user",
                "content": (
                    f"<earlier_turns>\n{history(state)}\n</earlier_turns>\n"
                    f"Customer's latest message: {state['sanitized_input']}"
                    + (
                        f"\n\nReviewer feedback on your previous draft: {state['critic_feedback']}"
                        if state.get("critic_feedback")
                        else ""
                    )
                ),
            }
        ]
        system = P.executor.format(language=state["language"])
        turn = deps.llm.with_tools("executor", system, messages, tools)
        out: dict[str, Any] = {"usage": [turn.usage.model_dump(mode="json")]}
        if turn.error and not turn.tool_calls and not turn.text:
            out["route_reason"] = "llm_failure"
            out["audit_trail"] = [event(deps, "executor", "llm_failure", error=turn.error)]
            return out

        messages = messages + [{"role": "assistant", "content": turn.assistant_content}]
        out["executor_messages"] = messages
        # Thought / Action trace (visible text only; hidden reasoning is not an audit artifact)
        out["react_steps"] = [
            {
                "turn_id": state["turn_id"],
                "iteration": state["react_iterations"],
                "thought": turn.text,
                "actions": [{"tool": c.name, "input": c.input} for c in turn.tool_calls],
            }
        ]
        pending, events = [], []
        for call in turn.tool_calls:
            if call.name == "escalate_to_human":
                try:
                    args = EscalateArgs.model_validate(call.input)
                except ValidationError:
                    args = EscalateArgs(reason="executor requested a human")
                out.update(
                    route_reason="executor_escalated",
                    critic_feedback=args.reason,
                    safety_violations=[],
                    escalation={"open_questions": args.open_questions},
                )
                events.append(event(deps, "executor", "escalation_requested", reason=args.reason))
                out["audit_trail"] = events
                return out
            if call.name == "propose_block_card":
                try:
                    args = ProposeBlockCardArgs.model_validate(call.input)
                except ValidationError as e:
                    pending.append(
                        {
                            "id": call.id,
                            "name": call.name,
                            "input": call.input,
                            "invalid": f"invalid_arguments: {e.error_count()} errors",
                        }
                    )
                    continue
                # Deterministic target check before bothering a supervisor: the card must
                # exist, belong to this customer, and still be blockable. Otherwise the
                # model gets the reason back as a tool error and tells the customer.
                check, _ = read(
                    "get_card_status", {"card_token": args.card_token}, tool_context(state, deps)
                )
                problem = None
                if not check.ok:
                    problem = check.error_code  # not_found, session_expired, ...
                elif check.data["status"] in ("Blocked", "Closed"):
                    problem = f"card_already_{check.data['status'].lower()}"
                if problem:
                    pending.append(
                        {
                            "id": call.id,
                            "name": call.name,
                            "input": call.input,
                            "invalid": f"cannot_propose: {problem}",
                        }
                    )
                    continue
                key = hashlib.sha256(
                    f"{state['thread_id']}|{state['turn_id']}|block_card|{args.card_token}".encode()
                ).hexdigest()[:32]
                out["proposed_action"] = {
                    "tool": "block_card",
                    "idempotency_key": key,
                    "summary": args.summary,
                    "args": {"card_token": args.card_token, "reason": args.reason},
                    # Evidence for the critic (not counted as "customer mentioned this card").
                    "target_check": {
                        k: check.data[k] for k in ("card_kind", "last4", "status", "card_token")
                    },
                }
                out["draft_response"] = turn.text or args.summary
                events.append(
                    event(
                        deps,
                        "executor",
                        "action_proposed",
                        tool="block_card",
                        card_token=args.card_token,
                        reason=args.reason,
                        idempotency_key=key,
                    )
                )
                out["audit_trail"] = events
                return out  # a proposal ends the loop: next stop is the critic
            pending.append({"id": call.id, "name": call.name, "input": call.input})
        if pending:
            out["pending_tool_calls"] = pending
        else:
            out["draft_response"] = turn.text
            events.append(event(deps, "executor", "draft_ready"))
        out["audit_trail"] = events
        return out

    def route_executor(state: GraphState) -> str:
        if state.get("route_reason"):
            return "escalate"
        if state.get("pending_tool_calls"):
            return "executor_tools"
        return "critic"

    def executor_tools(state: GraphState) -> dict:
        ctx = tool_context(state, deps)
        results, events = [], []
        read_results = list(state.get("read_results", []))
        failures = state.get("tool_failures", 0)
        route_reason = None
        for call in state["pending_tool_calls"]:
            retried = False
            if "invalid" in call:
                ok, content = False, {"error": call["invalid"]}
            else:
                r, retried = read(call["name"], call["input"], ctx)
                ok = r.ok
                content = r.data if r.ok else {"error": r.error_code, "detail": r.error}
                if r.ok:
                    read_results.append(
                        {"tool": call["name"], "input": call["input"], "output": r.data}
                    )
                elif r.error_code == "tool_unavailable":
                    failures += 1
                elif r.error_code in ("session_expired", "invalid_session"):
                    route_reason = "session_expired"
            events.append(
                event(
                    deps,
                    "executor_tools",
                    "tool_called",
                    tool=call["name"],
                    input=call["input"],
                    ok=ok,
                    error=None if ok else content.get("error"),
                    retried=retried,
                )
            )
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": call["id"],
                    "content": json.dumps(content, ensure_ascii=False, default=str),
                    "is_error": not ok,
                }
            )
        if failures >= 2 and route_reason is None:
            route_reason = "tool_failure"
        return {
            "executor_messages": state["executor_messages"]
            + [{"role": "user", "content": results}],
            "pending_tool_calls": [],
            "read_results": read_results,
            "tool_failures": failures,
            "react_iterations": state["react_iterations"] + 1,
            "route_reason": route_reason,
            "audit_trail": events,
        }

    def route_tools(state: GraphState) -> str:
        reason = state.get("route_reason")
        if reason == "session_expired":
            return "reject"
        if reason:
            return "escalate"
        return "executor"

    # --- policy RAG -------------------------------------------------------------------

    def policy_rag(state: GraphState) -> dict:
        question = state["sanitized_input"]
        hits = deps.retriever.search(question, k=4)
        docs = "\n".join(
            f'<document chunk_id="{h.chunk.chunk_id}" title="{h.chunk.doc_title}" '
            f'section="{h.chunk.section}">\n{h.chunk.text}\n</document>'
            for h in hits
        )
        feedback = (
            f"\n\nReviewer feedback on your previous answer: {state['critic_feedback']}"
            if state.get("critic_feedback")
            else ""
        )
        messages = [
            {"role": "user", "content": f"{docs}\n\nCustomer question: {question}{feedback}"}
        ]
        res = deps.llm.structured(
            "policy_rag",
            P.rag.format(language=state["language"]),
            messages,
            PolicyRagOutput,
        )
        out: dict[str, Any] = {
            "usage": [res.usage.model_dump(mode="json")],
            "rag_context": [
                {
                    "chunk_id": h.chunk.chunk_id,
                    "section": h.chunk.section,
                    "text": h.chunk.text,
                    "similarity": round(h.dense_similarity, 3),
                }
                for h in hits
            ],
        }
        if res.value is None:
            out["route_reason"] = "llm_failure"
            out["audit_trail"] = [event(deps, "policy_rag", "llm_failure", error=res.error)]
            return out
        v = res.value
        retrieved = {h.chunk.chunk_id for h in hits}
        valid = [c for c in v.citations if c in retrieved]
        violations = [f"invalid_citation:{c}" for c in v.citations if c not in retrieved]
        if v.answerable and not valid:
            violations.append("answer_without_citation")
        out.update(
            draft_response=v.grounded_answer,
            citations=valid,
            answerable=v.answerable,
            safety_violations=violations,
        )
        out["audit_trail"] = [
            event(
                deps,
                "policy_rag",
                "answered",
                answerable=v.answerable,
                citations=valid,
                retrieved=sorted(retrieved),
                violations=violations,
            )
        ]
        return out

    def route_rag(state: GraphState) -> str:
        return "escalate" if state.get("route_reason") else "critic"

    # --- critic -------------------------------------------------------------------------

    def critic(state: GraphState) -> dict:
        draft = state.get("draft_response", "")
        proposed = state.get("proposed_action")
        violations = list(state.get("safety_violations", []))
        violations += [f"raw_pii:{kind}" for kind in raw_pii_found(draft)]
        if proposed:
            if not deps.registry.is_write(proposed["tool"]):
                violations.append("unknown_action")
            token = proposed["args"]["card_token"]
            evidenced = token in json.dumps(state.get("read_results", [])) or token in state.get(
                "pii_token_refs", []
            )
            if not evidenced:  # COSO-style control: act only on a card we have seen
                violations.append("action_target_not_evidenced")

        evidence = (
            json.dumps(state.get("read_results", []), ensure_ascii=False, default=str)
            if state.get("intent") != Intent.CARD_POLICY
            else json.dumps(state.get("rag_context", []), ensure_ascii=False)
        )
        proposed_json = json.dumps(proposed, ensure_ascii=False) if proposed else "none"
        # With a proposed action the customer never sees the draft: they get a fixed
        # "requested, pending supervisor approval" message. Review the ACTION, not wording.
        # (Found live: the critic kept revising a draft nobody would read.)
        focus = (
            "\nNOTE: an action is proposed. The customer will NOT see the draft; the system "
            "sends a fixed message saying the block was requested and awaits supervisor "
            "approval. Judge the proposed action: right card (see target_check, verified by "
            "the system: it exists, belongs to the customer, and is blockable), a reason "
            "consistent with the request. Don't revise for draft wording.\n"
            if proposed
            else ""
        )
        messages = [
            {
                "role": "user",
                "content": (
                    f"Detected intent: {state.get('intent')}\n"
                    f"Customer message: {state['sanitized_input']}\n"
                    f"<evidence>\n{evidence[:12000]}\n</evidence>\n"
                    f"Proposed action: {proposed_json}{focus}"
                    f"\n<draft>\n{draft}\n</draft>"
                ),
            }
        ]
        res = deps.llm.structured(
            "critic",
            P.critic.format(language=state["language"]),
            messages,
            CriticOutput,
        )
        if res.value is None:  # fail closed: an unreviewed answer doesn't go out
            verdict, risk, feedback, approved = (
                "block",
                1.0,
                f"critic unavailable: {res.error}",
                None,
            )
        else:
            v = res.value
            verdict, risk, feedback = v.verdict.value, v.risk_score, v.feedback
            approved = v.approved_response
            violations += v.safety_violations
        if any(x.startswith(HARD_VIOLATIONS) for x in violations):
            verdict = "block"
        elif verdict == "approve" and any(
            x.startswith(("invalid_citation", "answer_without")) for x in violations
        ):
            verdict, feedback = "revise", feedback + " Cite only the provided chunk_ids."

        # THE HITL RULE, in code: any write, or a risky answer, needs a human.
        is_write = bool(proposed) and deps.registry.is_write(proposed["tool"])
        requires = is_write or risk > s.hitl_risk_threshold

        out: dict[str, Any] = {
            "usage": [res.usage.model_dump(mode="json")],
            "verdict": verdict,
            "risk_score": risk,
            "critic_feedback": feedback,
            "safety_violations": violations,
            "requires_human_approval": requires,
            "draft_response": (approved or draft) if verdict == "approve" else draft,
        }
        if verdict == "revise":
            if state.get("retries", 0) < s.max_critic_retries:
                out.update(
                    retries=state.get("retries", 0) + 1,
                    executor_messages=None,
                    proposed_action=None,
                    react_iterations=0,
                    pending_tool_calls=[],
                    safety_violations=[],
                )
            else:
                out["route_reason"] = "revisions_exhausted"
        elif verdict == "block":
            out["route_reason"] = "critic_blocked"
        elif requires and is_write:
            now = deps.clock()
            out["approval_request"] = {
                "idempotency_key": proposed["idempotency_key"],
                "requested_at": now.isoformat(),
                "expires_at": (now + timedelta(minutes=s.approval_ttl_minutes)).isoformat(),
            }
            # The customer hears right away what is happening, without claiming it is done.
            pending = mask_tokens(
                render("approval_pending", state["language"], card=proposed["args"]["card_token"]),
                state["thread_id"],
                deps.vault,
            )
            out["interim_response"] = pending
            out["messages"] = [{"role": "assistant", "content": pending}]
        elif requires:
            out["route_reason"] = "high_risk_answer"
        out["audit_trail"] = [
            event(
                deps,
                "critic",
                "reviewed",
                verdict=verdict,
                risk=risk,
                violations=violations,
                requires_human_approval=requires,
                llm_error=res.error,
            )
        ]
        return out

    def route_critic(state: GraphState) -> str:
        if state.get("route_reason"):
            return "escalate"
        if state["verdict"] == "revise":
            return "policy_rag" if state.get("intent") == Intent.CARD_POLICY else "executor"
        if state.get("approval_request"):
            return "human_approval"
        return "respond"

    # --- HITL -------------------------------------------------------------------------

    def human_approval(state: GraphState) -> dict:
        # Re-executed from the top when resumed: nothing before interrupt() has effects.
        req, proposed = state["approval_request"], state["proposed_action"]
        raw = interrupt(
            {
                "type": "approval_required",
                "thread_id": state["thread_id"],
                "requester_id": state["requester_id"],
                "action": proposed["tool"],
                "card": mask_tokens(proposed["args"]["card_token"], state["thread_id"], deps.vault),
                "reason": proposed["args"]["reason"],
                "summary": mask_tokens(proposed.get("summary", ""), state["thread_id"], deps.vault),
                "risk_score": state.get("risk_score"),
                "requested_at": req["requested_at"],
                "expires_at": req["expires_at"],
            }
        )
        now = deps.clock()
        try:
            decision = ApprovalInput.model_validate(raw)
        except ValidationError:
            decision = None
        outcome, note = Decision.REJECTED.value, ""
        if decision is None:
            note = "malformed approval input"
        elif now >= datetime.fromisoformat(req["expires_at"]):
            outcome, note = Decision.EXPIRED.value, "approval arrived after expiry"
        elif decision.decision == "approved":
            if decision.approver_id == state["requester_id"]:
                note = "approver cannot be the requester"
            elif decision.approver_role != "auditor":
                note = "approver lacks the auditor role"
            else:
                outcome = Decision.APPROVED.value
        human = {
            "decision": outcome,
            "approver_id": decision.approver_id if decision else None,
            "approver_role": decision.approver_role if decision else None,
            "comment": decision.comment if decision else "",
            "note": note,
            "ts": now.isoformat(),
        }
        out: dict[str, Any] = {
            "human_decision": human,
            "audit_trail": [
                event(
                    deps,
                    "human_approval",
                    "decided",
                    **{k: v for k, v in human.items() if k != "comment"},
                )
            ],
        }
        if outcome == Decision.APPROVED.value:
            out["approval_grant"] = {
                "idempotency_key": req["idempotency_key"],
                "decision": outcome,
                "approver_id": decision.approver_id,
                "approver_role": decision.approver_role,
                "requester_id": state["requester_id"],
                "decided_at": now.isoformat(),
                "expires_at": req["expires_at"],
            }
        return out

    def route_approval(state: GraphState) -> str:
        return "tool_executor" if state.get("approval_grant") else "respond"

    def tool_executor(state: GraphState) -> dict:
        """Deterministic: no LLM. Runs exactly the approved action."""
        proposed = state["proposed_action"]
        args = {**proposed["args"], "idempotency_key": proposed["idempotency_key"]}
        grant = ApprovalGrant(**state["approval_grant"])
        r = deps.registry.invoke(proposed["tool"], args, tool_context(state, deps), approval=grant)
        result = {"executed": r.ok, "verified": False, "output": r.data, "error_code": r.error_code}
        return {
            "action_result": result,
            "audit_trail": [
                event(
                    deps,
                    "tool_executor",
                    "executed",
                    tool=proposed["tool"],
                    ok=r.ok,
                    error=r.error_code,
                    output=r.data,
                )
            ],
        }

    def verify(state: GraphState) -> dict:
        """Independent re-read: only a confirmed state change is reported as done."""
        result = dict(state["action_result"])
        if result["executed"]:
            token = state["proposed_action"]["args"]["card_token"]
            r, _ = read("get_card_status", {"card_token": token}, tool_context(state, deps))
            result["verified"] = bool(r.ok and r.data["status"] == "Blocked")
            result["observed_status"] = r.data["status"] if r.ok else None
        out: dict[str, Any] = {
            "action_result": result,
            "audit_trail": [
                event(
                    deps,
                    "verify",
                    "verified",
                    verified=result["verified"],
                    observed_status=result.get("observed_status"),
                )
            ],
        }
        if not result["verified"]:
            out["route_reason"] = "action_not_verified"
        return out

    def route_verify(state: GraphState) -> str:
        return "respond" if state["action_result"]["verified"] else "escalate"

    # --- endings ----------------------------------------------------------------------

    def finish(
        state: GraphState,
        node: str,
        text: str,
        outcome: str,
        sources: list[str] | None = None,
        **extra,
    ) -> dict:
        text = mask_tokens(text, state["thread_id"], deps.vault)
        return {
            "final_response": text,
            "suggestions": extra.pop("choices", None) or suggestions(outcome, state["language"]),
            "messages": [{"role": "assistant", "content": text}],
            "audit_trail": [
                event(
                    deps,
                    node,
                    "responded",
                    outcome=outcome,
                    route_reason=state.get("route_reason"),
                    policy_sources=sources or [],
                )
            ],
            **extra,
        }

    def card_choices(state: GraphState) -> list[str] | None:
        """When Lía asks "which card?", offer the customer's cards as one-tap replies.

        Deterministic: taken from this turn's list_cards result (masked), filtered by the
        kind the customer mentioned and, for blocks, by cards that can still be blocked.
        """
        if not (state.get("draft_response") or "").rstrip().endswith("?"):
            return None
        listed = [r for r in state.get("read_results", []) if r["tool"] == "list_cards"]
        if not listed:
            return None
        cards = listed[-1]["output"]["cards"]
        text = state["sanitized_input"].lower()
        if "crédito" in text or "credito" in text:
            cards = [c for c in cards if c["card_kind"] == "credit"]
        elif "débito" in text or "debito" in text:
            cards = [c for c in cards if c["card_kind"] == "debit"]
        if state.get("intent") == Intent.CARD_BLOCK:
            cards = [c for c in cards if c["status"] == "Active"]
        return [f"{CARD_KIND_LABEL[c['card_kind']]} •••• {c['last4']}" for c in cards[:4]] or None

    def respond(state: GraphState) -> dict:
        lang, result = state["language"], state.get("action_result")
        human = state.get("human_decision")
        if result and result["verified"]:
            token = state["proposed_action"]["args"]["card_token"]
            output = result["output"] or {}
            key = "already_blocked" if output.get("already_blocked") else "blocked"
            ref = (output.get("action_id") or "-")[:8]
            return finish(
                state,
                "respond",
                render(key, lang, card=token, ref=ref),
                "blocked",
                sources=POLICY_SOURCES.get(key),
            )
        if human and human["decision"] != Decision.APPROVED.value:
            key = (
                "action_expired"
                if human["decision"] == Decision.EXPIRED.value
                else "action_cancelled"
            )
            text = render(key, lang, card=state["proposed_action"]["args"]["card_token"])
            return finish(state, "respond", text, "cancelled")
        return finish(
            state,
            "respond",
            state.get("draft_response") or "",
            state.get("intent") or "card_status",
            sources=state.get("citations"),
            choices=card_choices(state),
        )

    def reject(state: GraphState) -> dict:
        reason = state.get("route_reason") or (
            "injection" if state.get("injection_suspected") else "out_of_scope"
        )
        key = reason if reason in ("injection", "session_expired") else "out_of_scope"
        outcome = "session_expired" if reason == "session_expired" else "rejected"
        return finish(state, "reject", render(key, state["language"]), outcome, route_reason=reason)

    def escalate(state: GraphState) -> dict:
        reason = state.get("route_reason") or (
            "human_request" if state.get("intent") == Intent.HUMAN_REQUEST else "triage_failed"
        )
        digest = hashlib.sha256(f"{state['thread_id']}|{state['turn_id']}".encode())
        case_id = "CRD-" + digest.hexdigest()[:6].upper()
        result = state.get("action_result")
        handoff = HandoffPayload(
            case_id=case_id,
            reason=reason,
            customer_request=state["sanitized_input"],
            intent=state.get("intent"),
            language=state["language"] if state["language"] in ("es", "pt", "other") else "es",
            verified_facts=[
                json.dumps(
                    {"tool": r["tool"], "output": r["output"]}, ensure_ascii=False, default=str
                )[:500]
                for r in state.get("read_results", [])
            ],
            actions_taken=[json.dumps(result, default=str)] if result else [],
            evidence=state.get("citations", []),
            open_questions=(
                (state.get("escalation") or {}).get("open_questions", [])
                + ([state["critic_feedback"]] if state.get("critic_feedback") else [])
                + state.get("safety_violations", [])
            ),
        ).model_dump(mode="json")
        text = render("escalated", state["language"], case_id=case_id)
        return finish(state, "escalate", text, "escalated", route_reason=reason, escalation=handoff)

    # --- wiring -------------------------------------------------------------------------

    g = StateGraph(GraphState)
    for name, fn in [
        ("triage", triage),
        ("executor", executor),
        ("executor_tools", executor_tools),
        ("policy_rag", policy_rag),
        ("critic", critic),
        ("human_approval", human_approval),
        ("tool_executor", tool_executor),
        ("verify", verify),
        ("respond", respond),
        ("reject", reject),
        ("escalate", escalate),
    ]:
        g.add_node(name, fn)
    g.add_edge(START, "triage")
    g.add_conditional_edges(
        "triage", route_triage, ["reject", "escalate", "executor", "policy_rag"]
    )
    g.add_conditional_edges("executor", route_executor, ["executor_tools", "critic", "escalate"])
    g.add_conditional_edges("executor_tools", route_tools, ["executor", "escalate", "reject"])
    g.add_conditional_edges("policy_rag", route_rag, ["critic", "escalate"])
    g.add_conditional_edges(
        "critic", route_critic, ["executor", "policy_rag", "human_approval", "respond", "escalate"]
    )
    g.add_conditional_edges("human_approval", route_approval, ["tool_executor", "respond"])
    g.add_edge("tool_executor", "verify")
    g.add_conditional_edges("verify", route_verify, ["respond", "escalate"])
    for terminal in ("respond", "reject", "escalate"):
        g.add_edge(terminal, END)
    return g.compile(checkpointer=checkpointer or InMemorySaver())
