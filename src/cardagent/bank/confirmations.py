"""Customer confirmations: the record behind the "Yes, block it" button.

A write on the customer's own card is authorized by the customer, not by a supervisor
and never by the model. The graph opens a `pending` record and pauses; only the API, with
the customer's own session, moves it to `confirmed` or `cancelled`. `block_card` re-reads
the record and consumes it inside the write's transaction, so a confirmation authorizes
exactly one write, once.

Typed text never confirms: "sí" in the chat is just another message. The flag the tool
checks comes from the click.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from cardagent.bank.db import BankDB
from cardagent.bank.errors import ConfirmationInvalidError, NotFoundError
from cardagent.bank.sessions import utc_now

Status = Literal["pending", "confirmed", "cancelled", "expired"]
CustomerDecision = Literal["confirmed", "cancelled"]


@dataclass(frozen=True)
class Confirmation:
    confirmation_id: str
    thread_id: str
    customer_id: str
    idempotency_key: str
    card_token: str
    reason: str
    requires_supervisor: bool
    created_at: datetime
    expires_at: datetime
    status: str
    decided_by: str | None
    decided_at: datetime | None
    consumed_at: datetime | None

    @classmethod
    def from_row(cls, row) -> "Confirmation":
        dt = lambda v: datetime.fromisoformat(v) if v else None  # noqa: E731
        return cls(
            confirmation_id=row["confirmation_id"],
            thread_id=row["thread_id"],
            customer_id=row["customer_id"],
            idempotency_key=row["idempotency_key"],
            card_token=row["card_token"],
            reason=row["reason"],
            requires_supervisor=bool(row["requires_supervisor"]),
            created_at=dt(row["created_at"]),
            expires_at=dt(row["expires_at"]),
            status=row["status"],
            decided_by=row["decided_by"],
            decided_at=dt(row["decided_at"]),
            consumed_at=dt(row["consumed_at"]),
        )


class ConfirmationService:
    def __init__(self, db: BankDB, clock: Callable[[], datetime] = utc_now):
        self._db, self._clock = db, clock

    def open(
        self,
        *,
        confirmation_id: str,
        thread_id: str,
        customer_id: str,
        idempotency_key: str,
        card_token: str,
        reason: str,
        requires_supervisor: bool,
        ttl_minutes: int,
    ) -> Confirmation:
        """Idempotent: the graph node that calls this re-runs when the graph resumes."""
        now = self._clock()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO confirmations VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, NULL)",
                (
                    confirmation_id,
                    thread_id,
                    customer_id,
                    idempotency_key,
                    card_token,
                    reason,
                    int(requires_supervisor),
                    now.isoformat(),
                    (now + timedelta(minutes=ttl_minutes)).isoformat(),
                ),
            )
        return self.get(confirmation_id)

    def get(self, confirmation_id: str) -> Confirmation | None:
        row = self._db.one(
            "SELECT * FROM confirmations WHERE confirmation_id = ?", (confirmation_id,)
        )
        return Confirmation.from_row(row) if row else None

    def pending(self, thread_id: str) -> Confirmation | None:
        row = self._db.one(
            "SELECT * FROM confirmations WHERE thread_id = ? AND status = 'pending' "
            "ORDER BY created_at DESC LIMIT 1",
            (thread_id,),
        )
        return Confirmation.from_row(row) if row else None

    def decide(
        self, confirmation_id: str, decision: CustomerDecision, *, customer_id: str
    ) -> Confirmation:
        """The customer's click. `customer_id` comes from the session, never from a body."""
        current = self.get(confirmation_id)
        if current is None or current.customer_id != customer_id:
            raise NotFoundError("confirmation not found")  # another customer's: no hint
        if current.status != "pending":
            raise ConfirmationInvalidError(f"confirmation already {current.status}")
        now = self._clock()
        if now >= current.expires_at:
            self._set_status(confirmation_id, "expired", None, now)
            raise ConfirmationInvalidError("confirmation expired")
        self._set_status(confirmation_id, decision, customer_id, now)
        return self.get(confirmation_id)

    def expire_stale(self) -> list[Confirmation]:
        """Mark pending confirmations past their deadline; returns them for the caller."""
        now = self._clock()
        rows = self._db.query(
            "SELECT * FROM confirmations WHERE status = 'pending' AND expires_at <= ?",
            (now.isoformat(),),
        )
        stale = [Confirmation.from_row(r) for r in rows]
        for c in stale:
            self._set_status(c.confirmation_id, "expired", None, now)
        return stale

    def _set_status(
        self, confirmation_id: str, status: Status, decided_by: str | None, now: datetime
    ) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE confirmations SET status = ?, decided_by = ?, decided_at = ? "
                "WHERE confirmation_id = ? AND status = 'pending'",
                (status, decided_by, now.isoformat(), confirmation_id),
            )
