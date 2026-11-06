"""Custom Presidio recognizers for LATAM identifiers (CO / MX / AR).

How a pattern recognizer scores a match:
1. The regex matches and gets the pattern's base score.
2. If a context word (compared by lemma) appears near the match, Presidio's
   context enhancer boosts the score (+0.35 by default).
3. An optional `validate_result` can confirm (score -> 1.0) or reject (score -> 0)
   the match, e.g. a check digit.
Only matches at or above the guard's threshold (0.5) get tokenized. Ambiguous
formats (a bare 8-digit number) therefore start low and need context to qualify.
"""

from functools import lru_cache

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerRegistry
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_analyzer.predefined_recognizers import (
    CreditCardRecognizer,
    EmailRecognizer,
    SpacyRecognizer,
)

LANGUAGE = "es"

# Presidio entity type -> token label shown to the LLM, e.g. <TARJETA_1>.
ENTITY_LABELS: dict[str, str] = {
    "CO_CC": "CEDULA",
    "CO_NIT": "NIT",
    "MX_CURP": "CURP",
    "AR_DNI": "DNI",
    "CREDIT_CARD": "TARJETA",
    "BANK_ACCOUNT": "CUENTA",
    "CO_MOBILE": "CELULAR",
    "EMAIL_ADDRESS": "EMAIL",
    "PERSON": "PERSONA",
}
SUPPORTED_ENTITIES = list(ENTITY_LABELS)

# DIAN weights for the NIT check digit, applied right-to-left.
_NIT_WEIGHTS = (3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71)


def nit_check_digit(base: str) -> int:
    """Colombian NIT check digit (DIAN modulo-11 algorithm)."""
    total = sum(int(d) * w for d, w in zip(reversed(base), _NIT_WEIGHTS, strict=False))
    remainder = total % 11
    return remainder if remainder in (0, 1) else 11 - remainder


class ColombianNitRecognizer(PatternRecognizer):
    """NIT like 800.197.268-4: the digit after the dash must match the DIAN check digit."""

    def __init__(self) -> None:
        super().__init__(
            name="CO_NIT_recognizer",
            supported_entity="CO_NIT",
            supported_language=LANGUAGE,
            patterns=[Pattern("nit", r"\b\d{3}\.?\d{3}\.?\d{3}\s?-\s?\d\b", 0.4)],
            context=["nit", "empresa", "rut"],
        )

    def validate_result(self, pattern_text: str) -> bool:
        digits = "".join(c for c in pattern_text if c.isdigit())
        base, check = digits[:-1], int(digits[-1])
        return nit_check_digit(base) == check


def _pattern(entity: str, patterns: list[Pattern], context: list[str]) -> PatternRecognizer:
    # A unique name matters: Presidio's context enhancer looks recognizers up by name,
    # and with the default (shared) name it applies another recognizer's context words.
    return PatternRecognizer(
        name=f"{entity}_recognizer",
        supported_entity=entity,
        supported_language=LANGUAGE,
        patterns=patterns,
        context=context,
    )


def build_recognizers() -> list:
    return [
        # Colombian cédula: 6-10 digits, optionally with thousands dots. Ambiguous on its
        # own (could be an amount), so it only qualifies with context.
        _pattern(
            "CO_CC",
            [Pattern("cc", r"\b(?:\d{1,3}(?:\.\d{3}){2,3}|\d{6,10})\b", 0.3)],
            ["cédula", "cedula", "cc", "c.c", "documento", "identificación", "identificacion"],
        ),
        ColombianNitRecognizer(),
        # Mexican CURP: fixed structure (name letters, birth date, sex, state, consonants).
        _pattern(
            "MX_CURP",
            [
                Pattern(
                    "curp",
                    r"\b[A-Z][AEIOUX][A-Z]{2}\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])"
                    r"[HMX](?:AS|BC|BS|CC|CL|CM|CS|CH|DF|DG|GT|GR|HG|JC|MC|MN|MS|NT|NL|OC|PL"
                    r"|QT|QR|SP|SL|SR|TC|TS|TL|VZ|YN|ZS|NE)[B-DF-HJ-NP-TV-Z]{3}[A-Z\d]\d\b",
                    0.85,
                )
            ],
            ["curp"],
        ),
        # Argentine DNI: 7-8 digits, often dotted (30.123.456). Needs context.
        _pattern(
            "AR_DNI",
            [Pattern("dni", r"\b(?:\d{1,2}\.\d{3}\.\d{3}|\d{7,8})\b", 0.3)],
            ["dni"],
        ),
        # Colombian mobile: starts with 3, 10 digits, optional +57 prefix.
        _pattern(
            "CO_MOBILE",
            [
                Pattern(
                    "co_mobile",
                    r"(?<!\d)(?:\+?57[\s-]?)?3\d{2}[\s-]?\d{3}[\s-]?\d{4}(?!\d)",
                    0.55,
                )
            ],
            ["celular", "móvil", "movil", "teléfono", "telefono", "whatsapp", "llamar"],
        ),
        # Bank account: the dashed 3-6-2 format is distinctive; a bare long number needs context.
        _pattern(
            "BANK_ACCOUNT",
            [
                Pattern("account_dashed", r"\b\d{3}-\d{6}-\d{2}\b", 0.6),
                Pattern("account_digits", r"\b\d{9,20}\b", 0.2),
            ],
            ["cuenta", "ahorros", "corriente", "conta", "poupança"],
        ),
        # Card-like numbers WITHOUT Luhn: ~90% of the dataset's card numbers fail Luhn
        # (synthetic data), and they are exactly what customers will type in the demo.
        # Recall over precision: any 15-16 digit number is tokenized as a card.
        _pattern(
            "CREDIT_CARD",
            [
                Pattern(
                    "card_like",
                    r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{3,4}(?!\d)",
                    0.6,
                )
            ],
            ["tarjeta", "crédito", "credito", "débito", "debito", "cartão"],
        ),
        # Presidio's built-in card recognizer validates the Luhn check digit; a Luhn-valid
        # number scores 1.0 and wins over the card-like match above.
        CreditCardRecognizer(
            supported_language=LANGUAGE,
            context=["tarjeta", "crédito", "credito", "débito", "debito", "cartão", "visa"],
        ),
        EmailRecognizer(supported_language=LANGUAGE),
        # Person names come from spaCy's Spanish NER (label PER).
        SpacyRecognizer(supported_language=LANGUAGE, supported_entities=["PERSON"]),
    ]


@lru_cache
def build_analyzer(spacy_model: str = "es_core_news_md") -> AnalyzerEngine:
    """Build the analyzer once per process; loading spaCy takes a couple of seconds."""
    nlp_engine = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": LANGUAGE, "model_name": spacy_model}],
            "ner_model_configuration": {
                "model_to_presidio_entity_mapping": {"PER": "PERSON"},
                "labels_to_ignore": ["LOC", "ORG", "MISC"],
            },
        }
    ).create_engine()
    registry = RecognizerRegistry(supported_languages=[LANGUAGE])
    recognizers = build_recognizers()
    for recognizer in recognizers:
        registry.add_recognizer(recognizer)

    # Presidio only matches context against non-stopwords, and spaCy's Spanish model marks
    # some of our context words as stopwords ("cuenta" is also a form of "contar").
    # Un-stop every context word so it can actually boost a match.
    vocab = nlp_engine.nlp[LANGUAGE].vocab
    for recognizer in recognizers:
        for word in recognizer.context or []:
            vocab[word].is_stop = False

    return AnalyzerEngine(registry=registry, nlp_engine=nlp_engine, supported_languages=[LANGUAGE])
