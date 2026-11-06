"""Golden set: the case schema, loading, and per-case fixtures.

A case names a customer *profile* ("multi", "with_tx", ...) instead of a fixed customer id.
The fixture picks a real customer of that profile from bank.db, deterministically (hash of
the case id), and fills placeholders such as {credit_last4}. That spreads the cases across
segments and countries and keeps the golden file readable.

Placeholders:
    {credit_last4} {debit_last4}   the customer's active cards (last 4 digits)
    {credit_number}                the customer's full credit card number (PII guard test)
    {other_number}                 another customer's full card number (unauthorized access)
    {other_customer_id}            another customer's id
    {merchant}                     a merchant from the customer's recent card transactions
    {canary}                       a marker an injection asks the assistant to print

Fact markers (in `facts`; each item is "alt1|alt2|...", matched without case or accents):
    @credit_last4 @debit_last4 @owned_last4 @merchant
"""

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from cardagent.bank.db import BankDB
from cardagent.schemas.enums import Intent

GOLDEN_PATH = Path("eval/golden/golden_v1.jsonl")
GOLDEN_VERSION = "v1"
CANARY = "ZETA-4417"

Outcome = Literal[
    "resolve",  # answered with facts
    "clarify",  # asked the customer a question (no action taken)
    "abstain",  # policy not in the corpus: says so and offers a human
    "escalate",  # structured handoff to a human advisor
    "reject",  # safe refusal (out of scope, injection, expired session mid-turn)
    "block",  # card blocked (system: after human approval and verification)
    "cancelled",  # block requested, the human rejected it, nothing executed
    "session_rejected",  # expired session refused by the channel before any agent runs
]
Category = Literal[
    "normal_status",
    "normal_transactions",
    "normal_block",
    "policy_answerable",
    "policy_unanswerable",
    "ambiguous",
    "escalation",
    "injection_direct",
    "injection_indirect",
    "auth",
    "tool_failure",
    "out_of_scope",
]
Profile = Literal["multi", "one_credit", "with_blocked", "with_tx"]


class Setup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fault: str | None = None  # "always:<tool|*>" or "first:<tool|*>"
    session: Literal["expired_at_entry", "revoked_mid_turn"] | None = None
    poison: Literal["policy_chunk", "merchant_name"] | None = None


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    category: Category
    lang: Literal["es", "pt", "mixed"]
    customer: Profile
    message: str
    intent: Intent | None = None  # expected triage intent; None = not scored
    expect: list[Outcome] = Field(min_length=1)  # any of these is a correct outcome
    facts: list[str] = Field(default_factory=list)
    target: Literal["credit", "debit"] | None = None  # the card a block must hit
    hitl: Literal["approve", "reject"] | None = None  # the simulated auditor's decision
    setup: Setup = Field(default_factory=Setup)
    sensitive: bool = False  # loss/theft/fraud/dispute: no emojis allowed
    live: bool = False  # in the small paid subset


def load_golden(path: Path = GOLDEN_PATH) -> list[Case]:
    cases = [
        Case.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case ids in the golden set")
    return cases


def normalize(text: str) -> str:
    """Lowercase, no accents, single spaces: for robust fact matching."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text)


def stable_index(key: str, n: int) -> int:
    return int(hashlib.sha256(key.encode()).hexdigest(), 16) % n


# --- fixtures ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Card:
    product_id: str
    number: str
    last4: str
    kind: str
    status: str


@dataclass
class Fixture:
    customer_id: str
    segment: str
    country: str
    cards: list[Card]
    credit: Card | None
    debit: Card | None
    merchants: list[str]
    other_customer_id: str
    other_cards: list[Card]
    other_card: Card
    values: dict[str, str] = field(default_factory=dict)

    @property
    def owned_last4(self) -> set[str]:
        return {c.last4 for c in self.cards}

    def fill(self, text: str) -> str:
        def sub(match: re.Match) -> str:
            name = match.group(1)
            if name not in self.values:
                raise KeyError(f"placeholder {{{name}}} has no value for this customer")
            return self.values[name]

        return re.sub(r"\{(\w+)\}", sub, text)


PROFILE_SQL = {
    # >= 2 active credit cards and >= 1 active debit card
    "multi": """
        SELECT c.customer_id FROM customers c JOIN cards k USING (customer_id)
        WHERE c.customer_status = 'Active' GROUP BY c.customer_id
        HAVING sum(k.card_kind = 'credit' AND k.product_status = 'Active') >= 2
           AND sum(k.card_kind = 'debit' AND k.product_status = 'Active') >= 1
        ORDER BY c.customer_id""",
    # exactly one card, an active credit card
    "one_credit": """
        SELECT c.customer_id FROM customers c JOIN cards k USING (customer_id)
        WHERE c.customer_status = 'Active' GROUP BY c.customer_id
        HAVING count(*) = 1 AND max(k.card_kind) = 'credit'
           AND max(k.product_status) = 'Active'
        ORDER BY c.customer_id""",
    # at least one blocked card, plus an active credit card
    "with_blocked": """
        SELECT c.customer_id FROM customers c JOIN cards k USING (customer_id)
        WHERE c.customer_status = 'Active' GROUP BY c.customer_id
        HAVING sum(k.product_status = 'Blocked') >= 1
           AND sum(k.card_kind = 'credit' AND k.product_status = 'Active') >= 1
        ORDER BY c.customer_id""",
    # an active credit card with >= 3 transactions in the 30 days before as_of
    "with_tx": """
        SELECT k.customer_id FROM cards k JOIN customers c USING (customer_id)
        JOIN card_transactions t ON t.product_id = k.product_id
        WHERE c.customer_status = 'Active' AND k.card_kind = 'credit'
          AND k.product_status = 'Active' AND t.transaction_date >= :since
        GROUP BY k.customer_id HAVING count(*) >= 3
        ORDER BY k.customer_id""",
}


def _cards(db: BankDB, customer_id: str) -> list[Card]:
    return [
        Card(r["product_id"], r["product_number"], r["last4"], r["card_kind"], r["product_status"])
        for r in db.query(
            "SELECT * FROM cards WHERE customer_id = ? ORDER BY card_kind, last4", (customer_id,)
        )
    ]


def _primary(db: BankDB, cards: list[Card], kind: str) -> Card | None:
    """The active card of this kind with the most recent transaction (then lowest last4)."""
    active = [c for c in cards if c.kind == kind and c.status == "Active"]
    if not active:
        return None

    def latest(card: Card) -> str:
        row = db.one(
            "SELECT max(transaction_date) FROM card_transactions WHERE product_id = ?",
            (card.product_id,),
        )
        return row[0] or ""

    return sorted(active, key=lambda c: (latest(c), c.last4), reverse=True)[0]


class FixtureFactory:
    """Resolves a case's profile to a concrete customer in a given bank.db (read-only)."""

    def __init__(self, db: BankDB, as_of: date):
        self.db, self.as_of = db, as_of
        since = (as_of - timedelta(days=30)).isoformat()
        self.candidates = {
            name: [r[0] for r in db.query(sql, {"since": since} if ":since" in sql else ())]
            for name, sql in PROFILE_SQL.items()
        }
        for name, ids in self.candidates.items():
            if not ids:
                raise ValueError(f"no customer in bank.db matches profile '{name}'")

    def for_case(self, case: Case) -> Fixture:
        ids = self.candidates[case.customer]
        customer_id = ids[stable_index(case.id, len(ids))]
        row = self.db.one(
            "SELECT segment, country FROM customers WHERE customer_id = ?", (customer_id,)
        )
        cards = _cards(self.db, customer_id)
        credit, debit = _primary(self.db, cards, "credit"), _primary(self.db, cards, "debit")

        merchants: list[str] = []
        if credit:
            since = (self.as_of - timedelta(days=90)).isoformat()
            merchants = [
                r[0]
                for r in self.db.query(
                    "SELECT merchant_name FROM card_transactions WHERE product_id = ? "
                    "AND transaction_date >= ? AND merchant_name IS NOT NULL "
                    "ORDER BY transaction_date DESC",
                    (credit.product_id, since),
                )
            ]

        others = [c for c in self.candidates["multi"] if c != customer_id]
        other_id = others[stable_index(case.id + "/other", len(others))]
        other_cards = _cards(self.db, other_id)
        other_card = _primary(self.db, other_cards, "credit")
        assert other_card is not None  # "multi" guarantees an active credit card

        values = {
            "canary": CANARY,
            "other_number": other_card.number,
            "other_customer_id": other_id,
        }
        if credit:
            values |= {"credit_last4": credit.last4, "credit_number": credit.number}
        if debit:
            values["debit_last4"] = debit.last4
        if merchants:
            values["merchant"] = merchants[0]
        return Fixture(
            customer_id=customer_id,
            segment=row["segment"] or "unknown",
            country=row["country"] or "unknown",
            cards=cards,
            credit=credit,
            debit=debit,
            merchants=merchants,
            other_customer_id=other_id,
            other_cards=other_cards,
            other_card=other_card,
            values=values,
        )


def dump_cases(cases: list[Case]) -> str:
    """One JSON object per line, as stored in the golden file."""
    return "\n".join(
        json.dumps(c.model_dump(exclude_defaults=True), ensure_ascii=False) for c in cases
    )
