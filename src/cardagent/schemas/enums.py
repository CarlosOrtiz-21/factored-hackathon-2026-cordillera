"""Closed vocabularies. Anything an LLM returns outside these values fails validation."""

from enum import StrEnum


class Intent(StrEnum):
    CARD_STATUS = "card_status"
    CARD_TRANSACTIONS = "card_transactions"
    CARD_BLOCK = "card_block"
    CARD_POLICY = "card_policy"
    HUMAN_REQUEST = "human_request"
    OUT_OF_SCOPE = "out_of_scope"


class Urgency(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Language(StrEnum):
    ES = "es"
    PT = "pt"
    OTHER = "other"


class Verdict(StrEnum):
    APPROVE = "approve"
    REVISE = "revise"
    BLOCK = "block"


class Decision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class TriageSource(StrEnum):
    LLM = "llm"
    CLASSIFIER_FALLBACK = "classifier_fallback"
