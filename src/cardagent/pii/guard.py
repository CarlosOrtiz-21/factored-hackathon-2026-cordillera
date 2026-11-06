"""PII guard: the deterministic step that runs before any LLM call.

sanitize():    raw text -> text with tokens (+ references), mapping stored in the vault
reidentify():  tokens -> real values, only for tokens the caller explicitly allows
"""

from dataclasses import dataclass

from presidio_analyzer import AnalyzerEngine, RecognizerResult

from cardagent.pii.recognizers import ENTITY_LABELS, LANGUAGE, SUPPORTED_ENTITIES, build_analyzer
from cardagent.pii.vault import TOKEN_PATTERN, TokenVault, UnknownTokenError


@dataclass(frozen=True)
class DetectedEntity:
    """Detection metadata that is safe to log: no raw value, only the token."""

    entity_type: str
    token: str
    score: float


@dataclass(frozen=True)
class SanitizedText:
    text: str
    token_refs: list[str]
    entities: list[DetectedEntity]


# Phrases that introduce a name ("soy Carlos"), in Spanish and Portuguese.
_NAME_CUES = (
    "soy",
    "me llamo",
    "mi nombre es",
    "a nombre de",
    "titular",
    "sou",
    "me chamo",
    "meu nome é",
    "meu nome e",
)


def _is_plausible_person(text: str, result: RecognizerResult) -> bool:
    """Filter spaCy NER noise: it often tags sentence-initial words ('Gasté', 'Meu') as PER.

    Keep a PERSON span only if every word is capitalized and it is either a full name
    (2+ words) or preceded by a name cue. Trade-off: a lone first name without a cue
    ("Hola María") is not tokenized; a first name alone is low-sensitivity.
    """
    words = text[result.start : result.end].split()
    if not words or not all(w[0].isupper() for w in words):
        return False
    if len(words) >= 2:
        return True
    window = text[max(0, result.start - 25) : result.start].lower()
    return any(cue in window for cue in _NAME_CUES)


def _select_non_overlapping(results: list[RecognizerResult]) -> list[RecognizerResult]:
    """When spans overlap, keep the highest score (then the longest span)."""
    ranked = sorted(results, key=lambda r: (-r.score, -(r.end - r.start), r.start))
    kept: list[RecognizerResult] = []
    for candidate in ranked:
        if all(candidate.end <= k.start or candidate.start >= k.end for k in kept):
            kept.append(candidate)
    return sorted(kept, key=lambda r: r.start)


class PIIGuard:
    def __init__(
        self,
        vault: TokenVault,
        analyzer: AnalyzerEngine | None = None,
        score_threshold: float = 0.5,
    ) -> None:
        self._vault = vault
        self._analyzer = analyzer or build_analyzer()
        self._threshold = score_threshold

    def sanitize(self, thread_id: str, text: str) -> SanitizedText:
        # Portuguese input also goes through the Spanish pipeline: the regex recognizers
        # are language-agnostic, but PERSON recall in Portuguese is a known limitation.
        results = self._analyzer.analyze(
            text=text,
            language=LANGUAGE,
            entities=SUPPORTED_ENTITIES,
            score_threshold=self._threshold,
        )
        results = [r for r in results if r.entity_type != "PERSON" or _is_plausible_person(text, r)]
        spans = _select_non_overlapping(results)

        pieces: list[str] = []
        entities: list[DetectedEntity] = []
        cursor = 0
        for span in spans:
            label = ENTITY_LABELS[span.entity_type]
            token = self._vault.tokenize(thread_id, label, text[span.start : span.end])
            pieces += [text[cursor : span.start], token]
            entities.append(DetectedEntity(span.entity_type, token, round(span.score, 2)))
            cursor = span.end
        pieces.append(text[cursor:])

        refs = list(dict.fromkeys(e.token for e in entities))
        return SanitizedText(text="".join(pieces), token_refs=refs, entities=entities)

    def reidentify(self, thread_id: str, text: str, allowed_tokens: set[str]) -> str:
        """Replace tokens with real values, but only those in `allowed_tokens`.

        The allowlist is explicit on purpose: `respond` decides which values the
        customer may see (e.g. their own email) and everything else stays tokenized.
        """

        def replace(match) -> str:
            token = match.group(0)
            if token not in allowed_tokens:
                return token
            try:
                return self._vault.resolve(thread_id, token)
            except UnknownTokenError:
                return token

        return TOKEN_PATTERN.sub(replace, text)
