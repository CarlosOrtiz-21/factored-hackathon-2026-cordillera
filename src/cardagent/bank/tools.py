"""Card tools. Every tool:
- receives a ToolContext built from an authenticated, unexpired session;
- takes PII *tokens* (<TARJETA_1>), resolves them through the vault, and checks that the
  card belongs to the session's customer (another customer's card -> NotFoundError);
- returns tokens and last-4 digits, never full card numbers.

Read tools are safe to expose to the executor agent. The only write tool, `block_card`,
also demands a WriteAuthorization: the customer's own confirmation record (consumed in the
write's transaction, so it authorizes one write, once) and, when that record says the case
is risky, a supervisor's ApprovalGrant bound to the same idempotency key. Defense in depth:
even a buggy graph can't block a card without a human decision.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from cardagent.bank.db import BankDB
from cardagent.bank.errors import (
    ActionNotAllowedError,
    ApprovalInvalidError,
    ApprovalRequiredError,
    ConfirmationInvalidError,
    IdempotencyConflictError,
    NotFoundError,
)
from cardagent.bank.sessions import Session, utc_now
from cardagent.pii.vault import TokenVault, UnknownTokenError
from cardagent.schemas.enums import Decision

CARD_TOKEN = r"^<TARJETA_\d+>$"
CARD_LABEL = "TARJETA"


@dataclass(frozen=True)
class ToolContext:
    session: Session
    thread_id: str
    db: BankDB
    vault: TokenVault
    as_of: date  # "today" for the dataset (static snapshot ending 2026-06-17)
    clock: Callable[[], datetime] = utc_now


# --- argument models (the contract the LLM's tool calls must satisfy) ----------------


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(ToolArgs):
    pass


class CardArgs(ToolArgs):
    card_token: str = Field(pattern=CARD_TOKEN, description="Card token, e.g. <TARJETA_1>")


class TransactionsArgs(CardArgs):
    days: int = Field(default=30, ge=1, le=90, description="Look-back window in days")
    limit: int = Field(default=20, ge=1, le=50)


class BlockCardArgs(CardArgs):
    reason: Literal["lost", "stolen", "suspicious_activity", "customer_request"]
    idempotency_key: str = Field(min_length=8, max_length=64)


class ApprovalGrant(BaseModel):
    """A human decision, bound to ONE proposed action via its idempotency key."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    idempotency_key: str
    decision: Decision
    approver_id: str
    approver_role: str
    requester_id: str
    decided_at: datetime
    expires_at: datetime


class WriteAuthorization(BaseModel):
    """What a write needs: the customer's confirmation id, plus a supervisor's grant when
    the confirmation record is marked `requires_supervisor`. The record itself is re-read
    from the database by the tool; nothing here is trusted on its own."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    confirmation_id: str
    approval: ApprovalGrant | None = None


# --- helpers ------------------------------------------------------------------------


def _resolve_card(ctx: ToolContext, card_token: str):
    """Token -> card row owned by the session's customer. Anything else is 'not found'."""
    try:
        raw = ctx.vault.resolve(ctx.thread_id, card_token)
    except UnknownTokenError:
        raise NotFoundError("card not found") from None
    number = "".join(c for c in raw if c.isdigit())
    row = ctx.db.one(
        "SELECT * FROM cards WHERE product_number = ? AND customer_id = ?",
        (number, ctx.session.customer_id),
    )
    if row is None:
        raise NotFoundError("card not found")
    return row


def _card_view(ctx: ToolContext, row) -> dict:
    return {
        "card_token": ctx.vault.tokenize(ctx.thread_id, CARD_LABEL, row["product_number"]),
        "card_kind": row["card_kind"],
        "last4": row["last4"],
        "status": row["product_status"],
        "currency": row["currency"],
    }


# --- read tools ---------------------------------------------------------------------


def list_cards(ctx: ToolContext, args: NoArgs) -> dict:
    rows = ctx.db.query(
        "SELECT * FROM cards WHERE customer_id = ? ORDER BY card_kind, last4",
        (ctx.session.customer_id,),
    )
    return {"cards": [_card_view(ctx, r) for r in rows]}


def get_card_status(ctx: ToolContext, args: CardArgs) -> dict:
    row = _resolve_card(ctx, args.card_token)
    return {
        **_card_view(ctx, row),
        "credit_limit": row["credit_limit"],
        "current_balance": row["current_balance"],
        "expiration_date": row["expiration_date"],
        "last_transaction_date": row["last_transaction_date"],
    }


def get_card_transactions(ctx: ToolContext, args: TransactionsArgs) -> dict:
    row = _resolve_card(ctx, args.card_token)
    since = (ctx.as_of - timedelta(days=args.days)).isoformat()
    rows = ctx.db.query(
        "SELECT transaction_date, transaction_type, transaction_category, merchant_name, "
        "amount, currency, channel, transaction_country, transaction_status "
        "FROM card_transactions WHERE product_id = ? AND transaction_date >= ? "
        "ORDER BY transaction_date DESC LIMIT ?",
        (row["product_id"], since, args.limit),
    )
    return {
        "card_token": args.card_token,
        "window": {"from": since, "to": ctx.as_of.isoformat()},
        "transactions": [dict(r) for r in rows],
    }


# --- write tool ---------------------------------------------------------------------


def _check_approval(ctx: ToolContext, args: BlockCardArgs, approval: ApprovalGrant) -> None:
    now = ctx.clock()
    problems = []
    if approval.decision is not Decision.APPROVED:
        problems.append("not approved")
    if approval.idempotency_key != args.idempotency_key:
        problems.append("approval is for a different action")
    if approval.approver_role != "auditor":
        problems.append("approver lacks the auditor role")
    if approval.approver_id == approval.requester_id:
        problems.append("approver cannot be the requester (segregation of duties)")
    if approval.requester_id != ctx.session.customer_id:
        problems.append("approval was requested by another customer")
    if now >= approval.expires_at:
        problems.append("approval expired")
    if problems:
        raise ApprovalInvalidError("; ".join(problems))


def _check_confirmation(ctx: ToolContext, args: BlockCardArgs, auth: WriteAuthorization):
    """The customer's click, re-read from the database. Returns the row when it authorizes
    exactly this write; raises otherwise."""
    row = ctx.db.one(
        "SELECT * FROM confirmations WHERE confirmation_id = ?", (auth.confirmation_id,)
    )
    if (
        row is None
        or row["thread_id"] != ctx.thread_id
        or row["customer_id"] != ctx.session.customer_id
    ):
        raise ConfirmationInvalidError("no confirmation for this conversation")
    problems = []
    if row["status"] != "confirmed":
        problems.append(f"confirmation is {row['status']}, not confirmed")
    if row["decided_by"] != ctx.session.customer_id:
        problems.append("confirmation was not decided by the card holder")
    if row["idempotency_key"] != args.idempotency_key:
        problems.append("confirmation is for a different action")
    if row["card_token"] != args.card_token:
        problems.append("confirmation is for a different card")
    if row["reason"] != args.reason:
        problems.append("confirmation is for a different reason")
    if row["consumed_at"]:
        problems.append("confirmation already used")
    if ctx.clock() >= datetime.fromisoformat(row["expires_at"]):
        problems.append("confirmation expired")
    if problems:
        raise ConfirmationInvalidError("; ".join(problems))
    if row["requires_supervisor"]:
        if auth.approval is None:
            raise ApprovalRequiredError("this block also needs a supervisor's approval")
        _check_approval(ctx, args, auth.approval)
    return row


def _consume(conn, confirmation_id: str, now: datetime) -> None:
    """Inside the write's transaction: a confirmation authorizes one write, once."""
    cur = conn.execute(
        "UPDATE confirmations SET consumed_at = ? WHERE confirmation_id = ? "
        "AND consumed_at IS NULL",
        (now.isoformat(), confirmation_id),
    )
    if cur.rowcount != 1:
        raise ConfirmationInvalidError("confirmation already used")


def block_card(ctx: ToolContext, args: BlockCardArgs, auth: WriteAuthorization) -> dict:
    # Idempotency first: a retry of an action that already happened returns the original
    # result, even if its confirmation has lapsed since.
    previous = ctx.db.one(
        "SELECT * FROM card_actions WHERE idempotency_key = ?", (args.idempotency_key,)
    )
    card = _resolve_card(ctx, args.card_token)
    if previous is not None:
        if previous["product_id"] != card["product_id"]:
            raise IdempotencyConflictError("idempotency key already used for another card")
        return {
            "action_id": previous["action_id"],
            "status_before": previous["status_before"],
            "status_after": previous["status_after"],
            "replayed": True,
        }

    confirmation = _check_confirmation(ctx, args, auth)
    # Who authorized the write, as the audit row records it: the supervisor when one was
    # required, otherwise the card holder who clicked.
    approver = (
        auth.approval.approver_id if auth.approval else f"customer:{confirmation['decided_by']}"
    )
    now = ctx.clock()
    status = card["product_status"]
    if status == "Closed":
        raise ActionNotAllowedError("closed cards cannot be blocked")
    if status == "Blocked":
        with ctx.db.transaction() as conn:  # a verified no-op still uses up the confirmation
            _consume(conn, confirmation["confirmation_id"], now)
        return {
            "action_id": None,
            "status_before": status,
            "status_after": status,
            "replayed": False,
            "already_blocked": True,
        }

    action_id = str(uuid.uuid4())
    with ctx.db.transaction() as conn:
        _consume(conn, confirmation["confirmation_id"], now)
        conn.execute(
            "UPDATE cards SET product_status = 'Blocked' WHERE product_id = ?",
            (card["product_id"],),
        )
        after = conn.execute(
            "SELECT product_status FROM cards WHERE product_id = ?", (card["product_id"],)
        ).fetchone()["product_status"]
        conn.execute(
            "INSERT INTO card_actions VALUES (?, ?, ?, ?, 'block', ?, ?, ?, ?, ?, ?)",
            (
                action_id,
                args.idempotency_key,
                card["product_id"],
                ctx.session.customer_id,
                args.reason,
                ctx.session.customer_id,
                approver,
                status,
                after,
                now.isoformat(),
            ),
        )
    return {
        "action_id": action_id,
        "status_before": status,
        "status_after": after,
        "replayed": False,
    }
