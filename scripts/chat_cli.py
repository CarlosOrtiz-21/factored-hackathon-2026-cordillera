"""Terminal chat with Lía (Banco Cordillera, fictional) against the real graph and Claude.

    uv run python scripts/chat_cli.py                 # demo customer with the most cards
    uv run python scripts/chat_cli.py --customer CLI-...

Uses data/bank.db: blocks you approve are real in the demo database (reset with
`uv run python -m pipelines.etl.run --skip-extract --rebuild-bank`).
The customer's first name is used only here, in the channel layer (welcome message);
it never enters the graph, the LLM, or the audit trail.
"""

import argparse
import json
import uuid

from langgraph.types import Command

from cardagent.agents.guards import load_intent_fallback
from cardagent.agents.templates import progress, welcome
from cardagent.bank.db import BankDB
from cardagent.bank.registry import ToolRegistry
from cardagent.bank.sessions import SessionService
from cardagent.config import get_settings
from cardagent.embeddings import get_encoder
from cardagent.graph.build import Deps, build_graph
from cardagent.llm.client import AnthropicLLM, usage_cost
from cardagent.pii.guard import PIIGuard
from cardagent.pii.vault import InMemoryTokenVault
from cardagent.rag.corpus import load_corpus
from cardagent.rag.retriever import HybridRetriever
from cardagent.schemas.records import UsageRecord
from cardagent.schemas.state import turn_input

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"


def run(graph, payload, config, language: str) -> str:
    """Stream one graph run, printing progress; returns the last known language."""
    for chunk in graph.stream(payload, config, stream_mode="updates"):
        for node, update in chunk.items():
            if node == "__interrupt__":
                continue
            update = update or {}
            language = update.get("language", language)
            line = progress(node, language)
            if line:
                print(f"{DIM}  · {line}{RESET}")
            if update.get("interim_response"):
                print(f"\n{BOLD}Lía>{RESET} {update['interim_response']}\n")
    return language


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--customer")
    args = parser.parse_args()

    settings = get_settings()
    db = BankDB(settings.bank_db_path)
    customer = (
        args.customer
        or db.one(
            "SELECT customer_id FROM cards GROUP BY customer_id ORDER BY count(*) DESC LIMIT 1"
        )[0]
    )
    first_name = (
        db.one("SELECT first_name FROM customers WHERE customer_id = ?", (customer,))[0] or ""
    )
    vault = InMemoryTokenVault()
    guard = PIIGuard(vault)
    deps = Deps(
        llm=AnthropicLLM(),
        registry=ToolRegistry(),
        db=db,
        vault=vault,
        settings=settings,
        intent_fallback=load_intent_fallback(),
        retriever_factory=lambda: HybridRetriever(load_corpus(), get_encoder()),
    )
    graph = build_graph(deps)
    thread = f"cli-{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread}}
    sessions = SessionService(db, ttl_minutes=settings.session_ttl_minutes)
    _, session = sessions.issue(customer)  # stands in for the identity provider's login

    print(f"{DIM}[demo] customer {customer}; synthetic cards you can mention:")
    for row in db.query(
        "SELECT card_kind, product_number, product_status FROM cards WHERE customer_id = ?",
        (customer,),
    ):
        print(f"  {row['card_kind']:6} {row['product_number']}  {row['product_status']}")
    print(f"Empty line to quit.{RESET}\n")
    print(f"{BOLD}Lía>{RESET} {welcome(first_name)}\n")

    turn, language = 0, "es"
    while text := input("tú> ").strip():
        turn += 1
        clean = guard.sanitize(thread, text)
        print(f"{DIM}  [sanitized] {clean.text}{RESET}")
        language = run(
            graph,
            turn_input(
                thread_id=thread,
                session_id=session.session_id,
                customer_id=customer,
                session_expires_at=session.expires_at.isoformat(),
                turn_id=f"turn-{turn}",
                sanitized_input=clean.text,
                pii_token_refs=clean.token_refs,
            ),
            config,
            language,
        )

        while (snapshot := graph.get_state(config)).next == ("human_approval",):
            request = snapshot.tasks[0].interrupts[0].value
            print(f"{DIM}  === APPROVAL REQUIRED (auditor view) ===")
            print("  " + json.dumps(request, ensure_ascii=False, indent=2).replace("\n", "\n  "))
            answer = input("  approve? [y/N] ").strip().lower()
            approver = input("  approver id [auditor-demo]: ").strip() or "auditor-demo"
            print(RESET, end="")
            language = run(
                graph,
                Command(
                    resume={
                        "decision": "approved" if answer == "y" else "rejected",
                        "approver_id": approver,
                        "approver_role": "auditor",
                    }
                ),
                config,
                language,
            )

        state = graph.get_state(config).values
        print(f"\n{BOLD}Lía>{RESET} {state['final_response']}")
        if state.get("suggestions"):
            print(f"{DIM}  sugerencias: " + " | ".join(state["suggestions"]) + RESET)
        usage = [UsageRecord(**u) for u in state.get("usage", [])]
        print(
            f"{DIM}  [intent={state.get('intent')} source={state.get('triage_source')} "
            f"route={state.get('route_reason') or 'respond'} "
            f"cost so far=${usage_cost(usage):.4f}]{RESET}\n"
        )


if __name__ == "__main__":
    main()
