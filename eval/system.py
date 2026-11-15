"""Runs one case through the REAL system: channel checks + PII guard + the LangGraph graph.

It mirrors what the API does (api/main.py): an expired session is refused before the graph
(401), the message is sanitized, the graph runs, and if it pauses the evaluation plays the
humans with the case's scripted decision (`hitl`): first the customer's click on the
confirmation control (A1), then, for a risky case, the supervisor. A case without a
scripted decision gets "cancelled"/"rejected", the safe default.
"""

import json
import time
from datetime import timedelta

from langgraph.types import Command

from cardagent.bank.confirmations import ConfirmationService
from cardagent.bank.errors import SessionExpiredError
from cardagent.bank.sessions import SessionService, utc_now
from cardagent.config import Settings
from cardagent.graph.build import Deps, build_graph
from cardagent.llm.client import LLM, usage_cost
from cardagent.schemas.records import UsageRecord
from cardagent.schemas.state import turn_input
from eval.env import CaseEnv
from eval.trace import HUMAN_APPROVER, Trace, Write


def base_trace(env: CaseEnv, system: str) -> Trace:
    fx = env.fixture
    return Trace(
        system=system,
        case_id=env.case.id,
        customer_id=fx.customer_id,
        segment=fx.segment,
        country=fx.country,
        outcome="error",
    )


def channel_rejects(env: CaseEnv, token: str, settings: Settings) -> bool:
    """The API's session check, with the clock moved past the session's lifetime."""
    if env.case.setup.session != "expired_at_entry":
        return False
    late = timedelta(minutes=settings.session_ttl_minutes + 1)
    try:
        SessionService(env.db, clock=lambda: utc_now() + late).validate(token)
    except SessionExpiredError:
        return True
    return False


def actions_count(env: CaseEnv) -> int:
    return env.db.one("SELECT count(*) FROM card_actions")[0]


def new_writes(env: CaseEnv, before: int) -> list[Write]:
    rows = env.db.query(
        "SELECT a.customer_id, a.approver_id, a.status_before, a.status_after, c.last4 "
        "FROM card_actions a JOIN cards c ON c.product_id = a.product_id "
        "ORDER BY a.rowid LIMIT -1 OFFSET ?",
        (before,),
    )
    return [Write(**dict(r)) for r in rows]


def system_outcome(values: dict) -> str:
    responded = [e for e in values.get("audit_trail", []) if e["event"] == "responded"]
    node = responded[-1]["node"] if responded else None
    if node in ("reject", "escalate"):
        return node
    if node != "respond":
        return "error"
    customer = values.get("customer_decision")
    if customer and customer.get("decision") != "confirmed":  # the customer's click said no
        return "cancelled"
    result = values.get("action_result")
    if result and result.get("verified"):
        return "block"
    human = values.get("human_decision")
    if human and human.get("decision") != "approved":
        return "cancelled"
    if values.get("intent") == "card_policy" and values.get("answerable") is False:
        return "abstain"
    return "answer"  # resolve or clarify: told apart by the checks


def run_system(env: CaseEnv, settings: Settings, llm: LLM, intent_fallback) -> Trace:
    case, fx = env.case, env.fixture
    trace = base_trace(env, "system")
    token, session = env.sessions.issue(fx.customer_id)
    if channel_rejects(env, token, settings):
        trace.outcome = "session_rejected"
        return trace
    expires = session.expires_at
    if case.setup.session == "revoked_mid_turn":  # valid at the door, expired for the tools
        expires = utc_now() - timedelta(seconds=1)

    deps = Deps(
        llm=llm,
        registry=env.registry,
        db=env.db,
        vault=env.vault,
        settings=settings,
        intent_fallback=intent_fallback,
        retriever_factory=lambda: env.retriever,
    )
    graph = build_graph(deps)
    thread = f"eval-{case.id}"
    config = {"configurable": {"thread_id": thread}}
    clean = env.guard.sanitize(thread, fx.fill(case.message))
    before = actions_count(env)

    started = time.perf_counter()
    try:
        graph.invoke(
            turn_input(
                thread_id=thread,
                session_id=session.session_id,
                customer_id=fx.customer_id,
                session_expires_at=expires.isoformat(),
                turn_id="turn-1",
                sanitized_input=clean.text,
                pii_token_refs=clean.token_refs,
            ),
            config,
        )
        for _ in range(2):  # at most: the customer's click, then a supervisor
            waiting = graph.get_state(config).next
            if waiting == ("confirm",):
                trace.confirmation_requested = True
                req = graph.get_state(config).values["confirmation_request"]
                decision = "confirmed" if case.hitl == "approve" else "cancelled"
                # What the API does on the click: decide the record as the customer.
                ConfirmationService(env.db).decide(
                    req["confirmation_id"], decision, customer_id=fx.customer_id
                )
                graph.invoke(
                    Command(
                        resume={"confirmation_id": req["confirmation_id"], "decision": decision}
                    ),
                    config,
                )
            elif waiting == ("human_approval",):
                trace.approval_requested = True
                decision = "approved" if case.hitl == "approve" else "rejected"
                trace.approval_decision = decision
                graph.invoke(
                    Command(
                        resume={
                            "decision": decision,
                            "approver_id": HUMAN_APPROVER,
                            "approver_role": "auditor",
                            "comment": "evaluation",
                        }
                    ),
                    config,
                )
            else:
                break
    except Exception as e:  # a crash is a result too: recorded, never hidden
        trace.error = f"{type(e).__name__}: {e}"
    trace.latency_ms = round((time.perf_counter() - started) * 1000, 1)

    v = graph.get_state(config).values
    usage = [UsageRecord(**u) for u in v.get("usage", [])]
    trace.outcome = "error" if trace.error else system_outcome(v)
    trace.final_text = v.get("final_response", "")
    trace.intent = v.get("intent")
    trace.route = v.get("route_reason")
    trace.customer_decision = (v.get("customer_decision") or {}).get("decision")
    trace.writes = new_writes(env, before)
    trace.verified = bool((v.get("action_result") or {}).get("verified"))
    trace.tool_calls = [
        {k: e["payload"].get(k) for k in ("tool", "ok", "error")}
        for e in v.get("audit_trail", [])
        if e["event"] == "tool_called"
    ]
    trace.tool_outputs = json.dumps(v.get("read_results", []), ensure_ascii=False, default=str)
    trace.usage = [u.model_dump(mode="json") for u in usage]
    trace.cost_usd = round(usage_cost(usage), 6)
    trace.citations = v.get("citations", [])
    return trace
