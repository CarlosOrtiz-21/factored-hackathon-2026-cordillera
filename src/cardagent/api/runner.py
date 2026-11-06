"""Runs the graph for the API and turns each step into customer-facing events.

Events (SSE `event:` names):
    node_start         a node began; localized progress line ("Consultando tus tarjetas…")
    interim            "block requested, pending supervisor" (never claims it's done)
    approval_required  the turn is paused for a human; the customer waits on /events
    token              the FINAL, critic-reviewed text in small pieces (never raw LLM output)
    final              text + suggestions + route + case id + turn latency/cost
    error              generic, localized; details only in server logs
"""

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from langgraph.types import Command

from cardagent.agents.templates import progress
from cardagent.api.events import EventBus
from cardagent.bank.db import BankDB
from cardagent.bank.sessions import StaffSession
from cardagent.llm.client import usage_cost
from cardagent.schemas.records import UsageRecord

log = logging.getLogger("cardagent.api")

ERROR_TEXT = {
    "es": "Tuve un problema técnico y no pude completar tu solicitud. Intenta de nuevo en unos "
    "minutos; si es urgente, llama a la línea #2222.",
    "pt": "Tive um problema técnico e não consegui concluir sua solicitação. Tente de novo em "
    "alguns minutos; se for urgente, ligue para a linha #2222.",
}


def chunks(text: str, words: int = 4) -> list[str]:
    parts = text.split(" ")
    return [
        " ".join(parts[i : i + words]) + (" " if i + words < len(parts) else "")
        for i in range(0, len(parts), words)
    ]


@dataclass
class ApprovalOutcome:
    decision: str
    note: str
    final_response: str
    verified: bool


class TurnRunner:
    def __init__(self, graph, db: BankDB, bus: EventBus, clock):
        self.graph, self.db, self.bus, self.clock = graph, db, bus, clock
        self._thread_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _lock(self, thread_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._thread_locks.setdefault(thread_id, threading.Lock())

    @staticmethod
    def config(thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}}

    # --- queries --------------------------------------------------------------------

    def pending_request(self, thread_id: str):
        return self.db.one(
            "SELECT * FROM approval_requests WHERE thread_id = ? AND "
            "status = 'pending' ORDER BY requested_at DESC LIMIT 1",
            (thread_id,),
        )

    def state(self, thread_id: str) -> dict[str, Any]:
        return self.graph.get_state(self.config(thread_id)).values

    # --- running a turn -------------------------------------------------------------

    def run(self, thread_id: str, payload) -> None:
        """Blocking; call from a worker thread. Publishes events for `thread_id`."""
        with self._lock(thread_id):
            started = time.perf_counter()
            before = len(self.state(thread_id).get("usage", []))
            language = self.state(thread_id).get("language", "es")
            try:
                language = self._stream(thread_id, payload, language)
                self._after_run(thread_id, started, before)
            except Exception:  # never leak internals to the customer
                log.exception("turn failed thread=%s", thread_id)
                self.bus.publish(
                    thread_id,
                    "error",
                    {
                        "code": "internal_error",
                        "message": ERROR_TEXT.get(language, ERROR_TEXT["es"]),
                    },
                )
            finally:  # failed turns cost money too
                self._record_spend(thread_id, before)

    # --- budget cap (public demo) ------------------------------------------------------

    def _record_spend(self, thread_id: str, usage_before: int) -> None:
        try:
            usage = [UsageRecord(**u) for u in self.state(thread_id).get("usage", [])]
        except Exception:
            log.exception("could not read usage thread=%s", thread_id)
            return
        cost = usage_cost(usage[usage_before:])
        if cost <= 0:
            return
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO llm_spend VALUES (?, ?) "
                "ON CONFLICT(day) DO UPDATE SET usd = usd + excluded.usd",
                (self.clock().date().isoformat(), cost),
            )

    def spent_today(self) -> float:
        row = self.db.one(
            "SELECT usd FROM llm_spend WHERE day = ?", (self.clock().date().isoformat(),)
        )
        return float(row[0]) if row else 0.0

    def _stream(self, thread_id: str, payload, language: str) -> str:
        for mode, chunk in self.graph.stream(
            payload, self.config(thread_id), stream_mode=["tasks", "updates"]
        ):
            if mode == "tasks" and "result" not in chunk:
                line = progress(chunk["name"], language)
                if line:
                    self.bus.publish(
                        thread_id, "node_start", {"node": chunk["name"], "message": line}
                    )
            elif mode == "updates":
                for node, update in chunk.items():
                    if node == "__interrupt__" or not isinstance(update, dict):
                        continue
                    language = update.get("language", language)
                    if update.get("interim_response"):
                        self.bus.publish(thread_id, "interim", {"text": update["interim_response"]})
        return language

    def _after_run(self, thread_id: str, started: float, usage_before: int) -> None:
        snapshot = self.graph.get_state(self.config(thread_id))
        if snapshot.next == ("human_approval",):
            request = snapshot.tasks[0].interrupts[0].value
            self._record_pending(thread_id, snapshot.values["customer_id"], request)
            self.bus.publish(
                thread_id,
                "approval_required",
                {"status": "pending", "card": request["card"], "expires_at": request["expires_at"]},
            )
            return
        values = snapshot.values
        text = values.get("final_response", "")
        for piece in chunks(text):
            self.bus.publish(thread_id, "token", {"text": piece})
        turn_usage = [UsageRecord(**u) for u in values.get("usage", [])[usage_before:]]
        self.bus.publish(
            thread_id,
            "final",
            {
                "text": text,
                "suggestions": values.get("suggestions", []),
                "intent": values.get("intent"),
                "route": values.get("route_reason") or "respond",
                "case_id": (values.get("escalation") or {}).get("case_id"),
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "cost_usd": round(usage_cost(turn_usage), 5),
            },
        )

    def _record_pending(self, thread_id: str, customer_id: str, request: dict) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO approval_requests VALUES (?, ?, ?, ?, ?, 'pending', "
                "NULL, NULL)",
                (
                    thread_id,
                    request["requested_at"],
                    customer_id,
                    json.dumps(request, ensure_ascii=False),
                    request["expires_at"],
                ),
            )

    # --- approvals ------------------------------------------------------------------

    def decide(
        self, thread_id: str, decision: str, staff: StaffSession, comment: str = ""
    ) -> ApprovalOutcome | None:
        row = self.pending_request(thread_id)
        if row is None:
            return None
        self.run(
            thread_id,
            Command(
                resume={
                    "decision": decision,
                    "approver_id": staff.staff_id,
                    "approver_role": staff.role,
                    "comment": comment,
                }
            ),
        )
        values = self.state(thread_id)
        human = values.get("human_decision") or {}
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE approval_requests SET status = ?, decided_by = ?, decided_at = ? "
                "WHERE thread_id = ? AND requested_at = ?",
                (
                    human.get("decision", "rejected"),
                    staff.staff_id,
                    self.clock().isoformat(),
                    thread_id,
                    row["requested_at"],
                ),
            )
        return ApprovalOutcome(
            decision=human.get("decision", "rejected"),
            note=human.get("note", ""),
            final_response=values.get("final_response", ""),
            verified=bool((values.get("action_result") or {}).get("verified")),
        )

    def expire_stale(self) -> int:
        """Close approvals past their deadline so the customer is told (cancelled)."""
        now = self.clock()
        rows = self.db.query(
            "SELECT thread_id, expires_at FROM approval_requests WHERE status = 'pending'"
        )
        system = StaffSession("system-expiry", "system", now)
        expired = 0
        for row in rows:
            if now >= datetime.fromisoformat(row["expires_at"]):
                self.decide(row["thread_id"], "rejected", system)
                expired += 1
        return expired
