"""Before/after: the same scripted messages through prompts v1.0 and v1.1, side by side.

    uv run python scripts/compare_voice.py          # ~16 graph runs, well under $0.50

Writes docs/voice_comparison.md. Uses a COPY of data/bank.db, so nothing is blocked for real
(approvals are auto-rejected: the comparison is about wording, not actions).
"""

import shutil
import tempfile
import uuid
from pathlib import Path

from langgraph.types import Command

from cardagent.agents.guards import load_intent_fallback
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

SCRIPT = [
    "¿Mi tarjeta de crédito está activa?",
    "Muéstrame los últimos movimientos de mi tarjeta de débito",
    "Perdí mi tarjeta de crédito, bloquéala por favor",
    "¿Cuánto cuesta la reposición si me la robaron?",
    "Quero um financiamento imobiliário",
    "Quiero hablar con un asesor",
    "No reconozco un cargo en mi tarjeta",
    "Tengo un problema con mi tarjeta",
]


def run_version(version: str, db_path: Path) -> list[tuple[str, str, float]]:
    settings = get_settings().model_copy(update={"prompt_version": version})
    db = BankDB(db_path)
    customer = db.one(
        "SELECT customer_id FROM cards GROUP BY customer_id ORDER BY count(*) DESC LIMIT 1"
    )[0]
    rows = []
    for message in SCRIPT:
        vault = InMemoryTokenVault()
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
        thread = f"cmp-{uuid.uuid4().hex[:8]}"
        config = {"configurable": {"thread_id": thread}}
        _, session = SessionService(db).issue(customer)
        clean = PIIGuard(vault).sanitize(thread, message)
        state = graph.invoke(
            turn_input(
                thread_id=thread,
                session_id=session.session_id,
                customer_id=customer,
                session_expires_at=session.expires_at.isoformat(),
                turn_id="turn-1",
                sanitized_input=clean.text,
                pii_token_refs=clean.token_refs,
            ),
            config,
        )
        shown = state.get("interim_response") or ""
        if "__interrupt__" in state:
            state = graph.invoke(
                Command(
                    resume={
                        "decision": "rejected",
                        "approver_id": "cmp",
                        "approver_role": "auditor",
                    }
                ),
                config,
            )
        text = f"{shown}\n\n→ {state['final_response']}" if shown else state["final_response"]
        cost = usage_cost([UsageRecord(**u) for u in state.get("usage", [])])
        rows.append((message, text, cost))
    db.close()
    return rows


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_copy = Path(tmp) / "bank.db"
        shutil.copy(get_settings().bank_db_path, db_copy)
        before, after = run_version("v1.0", db_copy), run_version("v1.1", db_copy)
    lines = [
        "# Voice comparison: prompts v1.0 vs v1.1 (Lía)",
        "",
        "Offline, single run per message; approvals auto-rejected (wording only).",
        "",
    ]
    total = 0.0
    for (msg, a, ca), (_, b, cb) in zip(before, after, strict=True):
        total += ca + cb
        lines += [
            f"## {msg}",
            "",
            f"**v1.0** (${ca:.4f})",
            "",
            a,
            "",
            f"**v1.1** (${cb:.4f})",
            "",
            b,
            "",
        ]
    lines.append(f"Total cost: ${total:.4f}")
    Path("docs/voice_comparison.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
