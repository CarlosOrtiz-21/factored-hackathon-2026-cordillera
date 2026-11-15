"""A2: the reply check. Figures a draft states must exist in the evidence (code, not prompt).

Requirements: CTL-06, AI-03
"""

import pytest

from cardagent.agents.guards import evidence_numbers, unverified_figures

EVIDENCE = evidence_numbers(
    '{"last4": "4060", "credit_limit": 1250.0, "current_balance": 419.13, '
    '"expiration_date": "2027-05-31", "transaction_date": "2026-05-05 10:00:00"}',
    "#2222",
)


@pytest.mark.parametrize(
    "draft",
    [
        "Tu tarjeta terminada en 4060 está activa.",
        "Tienes un cupo de USD 1.250,00 y un saldo de USD 419,13.",  # separators differ
        "Vence el 31 de mayo de 2027.",
        "Tu última compra fue el 5 de mayo.",
        "Tienes 6 tarjetas activas.",  # a short count is derived, not read
        "Si es urgente, llama a la línea #2222.",  # a fixed brand fact
    ],
)
def test_figures_present_in_the_evidence_pass(draft):
    assert unverified_figures(draft, EVIDENCE) == []


@pytest.mark.parametrize(
    ("draft", "bad"),
    [
        ("Tienes un cupo de USD 9.999,00.", ["9.999,00"]),  # invented amount
        ("Tu tarjeta terminada en 1382 está activa.", ["1382"]),  # another card's digits
        ("Vence en 2029.", ["2029"]),  # invented year
        ("Tu saldo es USD 419,13 y el límite USD 2.500.", ["2.500"]),  # one of two
    ],
)
def test_figures_absent_from_the_evidence_are_flagged(draft, bad):
    assert unverified_figures(draft, EVIDENCE) == bad


def test_number_forms_cover_separators_and_decimals():
    assert evidence_numbers("1.250,00") == {"125000", "1250"}
    assert evidence_numbers("419,13 and 2026-05-05") == {"41913", "419", "2026", "05"}
