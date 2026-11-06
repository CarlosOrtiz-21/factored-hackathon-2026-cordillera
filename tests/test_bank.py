"""Step 2 tests: sessions, ownership, allowlist, approvals, idempotency, fault injection.

Uses a tiny SYNTHETIC bank.db built with the real export schema.
"""

import sqlite3
from datetime import UTC, date, datetime, timedelta

import pytest

from cardagent.bank.db import BankDB
from cardagent.bank.errors import (
    ActionNotAllowedError,
    ApprovalInvalidError,
    ApprovalRequiredError,
    IdempotencyConflictError,
    InvalidSessionError,
    NotFoundError,
    SessionExpiredError,
    ToolArgsError,
    ToolUnavailableError,
    UnregisteredToolError,
)
from cardagent.bank.registry import ToolRegistry
from cardagent.bank.sessions import SessionService
from cardagent.bank.tools import ApprovalGrant, ToolContext
from cardagent.pii.vault import InMemoryTokenVault
from cardagent.schemas.enums import Decision
from pipelines.etl.export import DDL

AS_OF = date(2026, 6, 17)
CARDS = {
    # product_id: (customer, number, kind, status)
    "P1": ("C1", "4028620856924060", "credit", "Active"),
    "P2": ("C1", "4187627731845008", "debit", "Blocked"),
    "P3": ("C1", "4000000000000002", "credit", "Closed"),
    "P4": ("C2", "5500000000000004", "credit", "Active"),
}


class Clock:
    def __init__(self):
        self.now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "bank.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(DDL)
        for cid in ("C1", "C2"):
            conn.execute(
                "INSERT INTO customers (customer_id, customer_status) VALUES (?, 'Active')", (cid,)
            )
        for pid, (cid, number, kind, status) in CARDS.items():
            conn.execute(
                "INSERT INTO cards (product_id, customer_id, card_kind, product_number, last4, "
                "currency, product_status) VALUES (?, ?, ?, ?, ?, 'USD', ?)",
                (pid, cid, kind, number, number[-4:], status),
            )
        conn.executemany(
            "INSERT INTO card_transactions (transaction_id, product_id, customer_id, "
            "transaction_date, amount, currency, merchant_name, transaction_status) "
            "VALUES (?, 'P1', 'C1', ?, 10.0, 'USD', 'Cafe', 'Approved')",
            [
                ("T1", "2026-06-10 10:00:00"),
                ("T2", "2026-06-01 10:00:00"),
                ("T3", "2026-01-01 10:00:00"),
            ],
        )
    bank = BankDB(path)
    yield bank
    bank.close()


@pytest.fixture
def sessions(db, clock):
    return SessionService(db, ttl_minutes=15, clock=clock)


@pytest.fixture
def vault():
    return InMemoryTokenVault()


def make_ctx(db, sessions, vault, clock, customer="C1", thread="t1"):
    _, session = sessions.issue(customer)
    return ToolContext(
        session=session, thread_id=thread, db=db, vault=vault, as_of=AS_OF, clock=clock
    )


def card_token(vault, thread, product_id):
    """What the PII guard does when the customer types their card number."""
    return vault.tokenize(thread, "TARJETA", CARDS[product_id][1])


def grant(clock, key, **overrides) -> ApprovalGrant:
    fields = dict(
        idempotency_key=key,
        decision=Decision.APPROVED,
        approver_id="auditor-7",
        approver_role="auditor",
        requester_id="C1",
        decided_at=clock(),
        expires_at=clock() + timedelta(minutes=10),
    )
    return ApprovalGrant(**{**fields, **overrides})


# --- sessions ---------------------------------------------------------------------


def test_session_lifecycle(db, sessions, clock):
    token, session = sessions.issue("C1")
    assert sessions.validate(token).customer_id == "C1"
    clock.advance(minutes=16)
    with pytest.raises(SessionExpiredError):
        sessions.validate(token)


def test_revoked_and_forged_tokens_are_rejected(sessions):
    token, _ = sessions.issue("C1")
    sessions.revoke(token)
    with pytest.raises(InvalidSessionError):
        sessions.validate(token)
    with pytest.raises(InvalidSessionError):
        sessions.validate("forged-token")


def test_session_tokens_are_stored_hashed(db, sessions):
    token, _ = sessions.issue("C1")
    stored = [r["token_sha256"] for r in db.query("SELECT token_sha256 FROM sessions")]
    assert token not in stored and len(stored[0]) == 64


def test_cannot_issue_session_for_unknown_customer(sessions):
    with pytest.raises(InvalidSessionError):
        sessions.issue("C999")


def test_session_expiring_mid_conversation_blocks_tools(db, sessions, vault, clock):
    ctx = make_ctx(db, sessions, vault, clock)
    clock.advance(minutes=20)
    with pytest.raises(SessionExpiredError):
        ToolRegistry().call("list_cards", {}, ctx)


# --- read tools and ownership -----------------------------------------------------


def test_list_cards_only_own_cards_and_no_full_numbers(db, sessions, vault, clock):
    ctx = make_ctx(db, sessions, vault, clock)
    out = ToolRegistry().call("list_cards", {}, ctx)
    assert {c["last4"] for c in out["cards"]} == {"4060", "5008", "0002"}
    assert "4028620856924060" not in repr(out)
    assert all(c["card_token"].startswith("<TARJETA_") for c in out["cards"])


def test_token_typed_by_customer_matches_listed_token(db, sessions, vault, clock):
    ctx = make_ctx(db, sessions, vault, clock)
    typed = card_token(vault, "t1", "P1")
    listed = ToolRegistry().call("list_cards", {}, ctx)["cards"]
    assert typed in {c["card_token"] for c in listed}


def test_other_customers_card_is_not_found(db, sessions, vault, clock):
    ctx = make_ctx(db, sessions, vault, clock, customer="C1")
    foreign = card_token(vault, "t1", "P4")  # belongs to C2
    with pytest.raises(NotFoundError):
        ToolRegistry().call("get_card_status", {"card_token": foreign}, ctx)


def test_token_from_another_thread_is_not_found(db, sessions, vault, clock):
    other = card_token(vault, "other-thread", "P1")
    ctx = make_ctx(db, sessions, vault, clock, thread="t1")
    with pytest.raises(NotFoundError):
        ToolRegistry().call("get_card_status", {"card_token": other}, ctx)


def test_transactions_respect_window(db, sessions, vault, clock):
    ctx = make_ctx(db, sessions, vault, clock)
    out = ToolRegistry().call(
        "get_card_transactions", {"card_token": card_token(vault, "t1", "P1"), "days": 30}, ctx
    )
    assert [t["transaction_date"][:10] for t in out["transactions"]] == ["2026-06-10", "2026-06-01"]


# --- allowlist and argument validation --------------------------------------------


def test_unregistered_tool_is_rejected(db, sessions, vault, clock):
    ctx = make_ctx(db, sessions, vault, clock)
    with pytest.raises(UnregisteredToolError):
        ToolRegistry().call("transfer", {"amount": 100}, ctx)


@pytest.mark.parametrize(
    "args",
    [
        {"card_token": "<TARJETA_1>", "days": 1000},  # out of range
        {"card_token": "4028620856924060"},  # raw number, not token
        {"card_token": "<TARJETA_1>", "include_other_customers": True},  # extra field
    ],
)
def test_invalid_arguments_are_rejected(db, sessions, vault, clock, args):
    ctx = make_ctx(db, sessions, vault, clock)
    with pytest.raises(ToolArgsError):
        ToolRegistry().call("get_card_transactions", args, ctx)


def test_read_only_registry_has_no_write_tools(db, sessions, vault, clock):
    ro = ToolRegistry().read_only()
    assert "block_card" not in {d["name"] for d in ro.definitions()}
    ctx = make_ctx(db, sessions, vault, clock)
    with pytest.raises(UnregisteredToolError):
        ro.call("block_card", {}, ctx)


def test_invoke_returns_structured_errors(db, sessions, vault, clock):
    ctx = make_ctx(db, sessions, vault, clock)
    result = ToolRegistry().invoke("transfer", {}, ctx)
    assert (result.ok, result.error_code) == (False, "unregistered_tool")


def test_fault_injection_simulates_outage(db, sessions, vault, clock):
    def outage(name):
        raise ToolUnavailableError(f"{name} timed out")

    ctx = make_ctx(db, sessions, vault, clock)
    result = ToolRegistry(fault_injector=outage).invoke("list_cards", {}, ctx)
    assert result.error_code == "tool_unavailable"


# --- block_card: approval, idempotency, state rules -------------------------------


def block_args(vault, pid="P1", key="key-00000001", reason="lost"):
    return {"card_token": card_token(vault, "t1", pid), "reason": reason, "idempotency_key": key}


def status_of(db, pid):
    return db.one("SELECT product_status FROM cards WHERE product_id = ?", (pid,))[0]


def test_block_requires_approval(db, sessions, vault, clock):
    ctx = make_ctx(db, sessions, vault, clock)
    with pytest.raises(ApprovalRequiredError):
        ToolRegistry().call("block_card", block_args(vault), ctx)
    assert status_of(db, "P1") == "Active"


@pytest.mark.parametrize(
    ("overrides", "problem"),
    [
        ({"decision": Decision.REJECTED}, "not approved"),
        ({"approver_id": "C1"}, "segregation of duties"),
        ({"approver_role": "customer"}, "auditor role"),
        ({"idempotency_key": "key-OTHER-ACTION"}, "different action"),
        ({"requester_id": "C2"}, "another customer"),
    ],
)
def test_invalid_approvals_are_refused(db, sessions, vault, clock, overrides, problem):
    ctx = make_ctx(db, sessions, vault, clock)
    with pytest.raises(ApprovalInvalidError, match=problem):
        ToolRegistry().call(
            "block_card", block_args(vault), ctx, approval=grant(clock, "key-00000001", **overrides)
        )
    assert status_of(db, "P1") == "Active"


def test_expired_approval_is_refused(db, sessions, vault, clock):
    ctx = make_ctx(db, sessions, vault, clock)
    approval = grant(clock, "key-00000001")
    clock.advance(minutes=11)  # approval TTL is 10 minutes; session still valid (15)
    with pytest.raises(ApprovalInvalidError, match="expired"):
        ToolRegistry().call("block_card", block_args(vault), ctx, approval=approval)


def test_approved_block_changes_state_and_is_audited(db, sessions, vault, clock):
    ctx = make_ctx(db, sessions, vault, clock)
    out = ToolRegistry().call(
        "block_card", block_args(vault), ctx, approval=grant(clock, "key-00000001")
    )
    assert (out["status_before"], out["status_after"]) == ("Active", "Blocked")
    # verification = an independent re-read through a read tool
    verified = ToolRegistry().call(
        "get_card_status", {"card_token": card_token(vault, "t1", "P1")}, ctx
    )
    assert verified["status"] == "Blocked"
    action = db.one("SELECT * FROM card_actions")
    assert (action["approver_id"], action["requester_id"], action["reason"]) == (
        "auditor-7",
        "C1",
        "lost",
    )


def test_retry_with_same_key_does_not_act_twice(db, sessions, vault, clock):
    ctx = make_ctx(db, sessions, vault, clock)
    registry, approval = ToolRegistry(), grant(clock, "key-00000001")
    first = registry.call("block_card", block_args(vault), ctx, approval=approval)
    second = registry.call("block_card", block_args(vault), ctx, approval=approval)
    assert second["replayed"] and second["action_id"] == first["action_id"]
    assert db.one("SELECT count(*) FROM card_actions")[0] == 1


def test_same_key_for_different_card_conflicts(db, sessions, vault, clock):
    ctx = make_ctx(db, sessions, vault, clock)
    registry = ToolRegistry()
    registry.call("block_card", block_args(vault), ctx, approval=grant(clock, "key-00000001"))
    db.query("UPDATE cards SET product_status = 'Active' WHERE product_id = 'P2'")
    with pytest.raises(IdempotencyConflictError):
        registry.call(
            "block_card", block_args(vault, pid="P2"), ctx, approval=grant(clock, "key-00000001")
        )


def test_already_blocked_card_is_a_verified_no_op(db, sessions, vault, clock):
    ctx = make_ctx(db, sessions, vault, clock)
    out = ToolRegistry().call(
        "block_card",
        block_args(vault, pid="P2", key="key-00000002"),
        ctx,
        approval=grant(clock, "key-00000002"),
    )
    assert out["already_blocked"] and out["status_after"] == "Blocked"
    assert db.one("SELECT count(*) FROM card_actions")[0] == 0


def test_closed_card_cannot_be_blocked(db, sessions, vault, clock):
    ctx = make_ctx(db, sessions, vault, clock)
    with pytest.raises(ActionNotAllowedError):
        ToolRegistry().call(
            "block_card",
            block_args(vault, pid="P3", key="key-00000003"),
            ctx,
            approval=grant(clock, "key-00000003"),
        )


def test_cannot_block_another_customers_card_even_with_approval(db, sessions, vault, clock):
    ctx = make_ctx(db, sessions, vault, clock, customer="C1")
    with pytest.raises(NotFoundError):
        ToolRegistry().call(
            "block_card",
            block_args(vault, pid="P4", key="key-00000004"),
            ctx,
            approval=grant(clock, "key-00000004"),
        )
    assert status_of(db, "P4") == "Active"
