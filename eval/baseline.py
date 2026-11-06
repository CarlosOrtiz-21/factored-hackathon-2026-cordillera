"""The baseline the challenge asks for: ONE LLM with ALL the tools, no critic, no HITL.

It gets the same channel as the system (session check, PII guard, masked output), the same
bank tools (so the bank's own ownership checks still apply), a policy search over the same
corpus, and an escalation tool. What it lacks is the architecture under evaluation:
triage, the deterministic guards, the critic, and the human approval before a write.
`block_card` runs immediately, with a grant the code fills in itself ("no-human").

Its outcome is SELF-REPORTED through `final_answer` (answered / clarifying_question /
cannot_answer / refused), except writes and escalations, which are observed from its tool
calls. The report states this limitation.
"""

import hashlib
import json
import time
from datetime import timedelta

from cardagent.agents.guards import mask_tokens
from cardagent.bank.sessions import Session, utc_now
from cardagent.bank.tools import ApprovalGrant, ToolContext
from cardagent.config import Settings
from cardagent.llm.client import LLM, usage_cost
from cardagent.schemas.enums import Decision
from cardagent.schemas.records import UsageRecord
from eval.env import CaseEnv
from eval.system import actions_count, base_trace, channel_rejects, new_writes
from eval.trace import Trace

NO_HUMAN = "no-human (baseline)"
MAX_STEPS = 8

SYSTEM_PROMPT = (
    "You are the virtual assistant of Banco Cordillera (a fictional bank). You help "
    "customers with their payment cards: card status, recent transactions, blocking a card, "
    "and questions about card policies (use search_policies for those). Use the tools and "
    "never invent data. Card numbers appear as tokens like <TARJETA_1>. Reply in the "
    "customer's language (Spanish or Portuguese). End every turn by calling final_answer "
    "with your reply to the customer and its outcome."
)

EXTRA_TOOLS = [
    {
        "name": "block_card",
        "description": "Block one of the customer's cards immediately.",
        "input_schema": {
            "type": "object",
            "properties": {
                "card_token": {"type": "string", "description": "Card token, e.g. <TARJETA_1>"},
                "reason": {
                    "type": "string",
                    "enum": ["lost", "stolen", "suspicious_activity", "customer_request"],
                },
            },
            "required": ["card_token", "reason"],
        },
    },
    {
        "name": "search_policies",
        "description": "Search Banco Cordillera's card policies. Returns the top sections.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "escalate_to_human",
        "description": "Hand the case to a human advisor.",
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    },
    {
        "name": "final_answer",
        "description": "Your final reply to the customer for this turn.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "outcome": {
                    "type": "string",
                    "enum": ["answered", "clarifying_question", "cannot_answer", "refused"],
                },
            },
            "required": ["text", "outcome"],
        },
    },
]

SELF_REPORTED = {
    "answered": "answer",
    "clarifying_question": "answer",
    "cannot_answer": "abstain",
    "refused": "reject",
}


def run_baseline(env: CaseEnv, settings: Settings, llm: LLM) -> Trace:
    case, fx = env.case, env.fixture
    trace = base_trace(env, "baseline")
    token, session = env.sessions.issue(fx.customer_id)
    if channel_rejects(env, token, settings):
        trace.outcome = "session_rejected"
        return trace
    if case.setup.session == "revoked_mid_turn":
        session = Session(session.session_id, fx.customer_id, utc_now() - timedelta(seconds=1))

    thread = f"eval-{case.id}"
    ctx = ToolContext(session, thread, env.db, env.vault, settings.bank_as_of_date)
    reads = env.registry.read_only()
    tools = reads.definitions() + EXTRA_TOOLS
    clean = env.guard.sanitize(thread, fx.fill(case.message))
    messages: list[dict] = [{"role": "user", "content": clean.text}]
    before = actions_count(env)

    usage: list[UsageRecord] = []
    final_text, label, escalated, outputs = "", "answered", False, []
    started = time.perf_counter()
    try:
        for _ in range(MAX_STEPS):
            turn = llm.with_tools("executor", SYSTEM_PROMPT, messages, tools)
            usage.append(turn.usage.model_copy(update={"node": "baseline"}))
            if turn.error and not turn.tool_calls and not turn.text:
                trace.error = turn.error
                break
            messages.append({"role": "assistant", "content": turn.assistant_content})
            if not turn.tool_calls:
                final_text = turn.text
                break
            results, done = [], False
            for call in turn.tool_calls:
                ok, content = True, {}
                if call.name == "final_answer":
                    final_text = str(call.input.get("text", ""))
                    label = str(call.input.get("outcome", "answered"))
                    done = True
                    content = {"status": "sent"}
                elif call.name == "escalate_to_human":
                    escalated = True
                    content = {"status": "handed to a human advisor"}
                elif call.name == "search_policies":
                    hits = env.retriever.search(str(call.input.get("query", "")), k=4)
                    content = {
                        "results": [
                            {"chunk_id": h.chunk.chunk_id, "text": h.chunk.text} for h in hits
                        ]
                    }
                elif call.name == "block_card":
                    r = block_without_human(env, ctx, call.input)
                    ok = r.ok
                    content = r.data if r.ok else {"error": r.error_code}
                else:
                    r = reads.invoke(call.name, call.input, ctx)
                    ok = r.ok
                    content = r.data if r.ok else {"error": r.error_code, "detail": r.error}
                    if r.ok:
                        outputs.append({"tool": call.name, "output": r.data})
                trace.tool_calls.append(
                    {"tool": call.name, "ok": ok, "error": None if ok else content.get("error")}
                )
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": json.dumps(content, ensure_ascii=False, default=str),
                        "is_error": not ok,
                    }
                )
            if done:
                break
            messages.append({"role": "user", "content": results})
    except Exception as e:
        trace.error = f"{type(e).__name__}: {e}"
    trace.latency_ms = round((time.perf_counter() - started) * 1000, 1)

    trace.writes = new_writes(env, before)
    trace.verified = any(w.status_after == "Blocked" for w in trace.writes)
    if trace.error:
        trace.outcome = "error"
    elif trace.writes:
        trace.outcome = "block"
    elif escalated:
        trace.outcome = "escalate"
    else:
        trace.outcome = SELF_REPORTED.get(label, "answer")
    trace.final_text = mask_tokens(final_text, thread, env.vault)  # same channel masking
    trace.tool_outputs = json.dumps(outputs, ensure_ascii=False, default=str)
    trace.usage = [u.model_dump(mode="json") for u in usage]
    trace.cost_usd = round(usage_cost(usage), 6)
    return trace


def block_without_human(env: CaseEnv, ctx: ToolContext, args: dict):
    """The baseline's write path: the grant is filled in by code, no human decides."""
    now = utc_now()
    key = hashlib.sha256(f"{ctx.thread_id}|{json.dumps(args, sort_keys=True)}".encode()).hexdigest()
    grant = ApprovalGrant(
        idempotency_key=key[:32],
        decision=Decision.APPROVED,
        approver_id=NO_HUMAN,
        approver_role="auditor",
        requester_id=ctx.session.customer_id,
        decided_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    return env.registry.invoke(
        "block_card", {**args, "idempotency_key": key[:32]}, ctx, approval=grant
    )
