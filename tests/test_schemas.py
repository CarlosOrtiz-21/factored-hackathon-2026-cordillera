"""Step 1 tests: the schemas reject anything outside the contract."""

import pytest
from pydantic import ValidationError

from cardagent.schemas.agents import (
    CriticOutput,
    ProposeBlockCardArgs,
    ProposedAction,
    TriageOutput,
)
from cardagent.schemas.enums import Intent
from cardagent.schemas.records import AuditEvent

VALID_TRIAGE = {
    "intent": "card_block",
    "urgency": "high",
    "language": "es",
    "injection_suspected": False,
    "rationale": "El cliente reporta pérdida de la tarjeta.",
}


def test_valid_triage_parses_into_enums():
    out = TriageOutput.model_validate(VALID_TRIAGE)
    assert out.intent is Intent.CARD_BLOCK


def test_intent_outside_vocabulary_is_rejected():
    # 'transfer' was removed from scope: an LLM returning it must fail validation.
    with pytest.raises(ValidationError):
        TriageOutput.model_validate({**VALID_TRIAGE, "intent": "transfer"})


def test_unexpected_fields_are_rejected():
    with pytest.raises(ValidationError):
        TriageOutput.model_validate({**VALID_TRIAGE, "approve_everything": True})


def test_risk_score_must_be_between_0_and_1():
    with pytest.raises(ValidationError):
        CriticOutput.model_validate({"risk_score": 1.5, "verdict": "approve", "feedback": "ok"})


def test_proposed_action_requires_idempotency_key():
    with pytest.raises(ValidationError):
        ProposedAction.model_validate({"tool": "block_card", "args": {"card_token": "<TARJETA_1>"}})


def test_block_proposal_rejects_raw_card_numbers_and_unknown_reasons():
    with pytest.raises(ValidationError):
        ProposeBlockCardArgs.model_validate(
            {"card_token": "4028620856924060", "reason": "lost", "summary": "x"}
        )
    with pytest.raises(ValidationError):
        ProposeBlockCardArgs.model_validate(
            {"card_token": "<TARJETA_1>", "reason": "because I say so", "summary": "x"}
        )


def test_audit_events_are_immutable():
    event = AuditEvent(node="triage", event="classified", payload={"intent": "card_block"})
    with pytest.raises(ValidationError):
        event.node = "critic"


def test_long_triage_rationale_is_truncated_not_rejected():
    # Regression from the first live run: Haiku wrote >300 chars and triage was discarded.
    out = TriageOutput.model_validate({**VALID_TRIAGE, "rationale": "x" * 900})
    assert len(out.rationale) == 300 and out.intent is Intent.CARD_BLOCK
