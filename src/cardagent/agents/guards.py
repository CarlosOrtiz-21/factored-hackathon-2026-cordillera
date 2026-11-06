"""Deterministic guards: rules in code, not in prompts.

They run alongside the LLMs and never depend on them: the LLM can add a flag, but it
can't remove one raised here.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

from cardagent.pii.vault import TOKEN_PATTERN, TokenVault, UnknownTokenError

# Direct prompt-injection signals (ES / PT / EN). A match forces injection_suspected.
# A BACKUP layer: the LLM triage is the primary detector. One alternative per attack
# family. Tuned on datasets/injection_dev_v1.csv (with tricky benign negatives) and checked
# for false positives on the intent training utterances; never tuned on the golden set.
_W = r"(?:\W+\w+){0,4}?\W+"  # up to 4 words in between ("ignora por favor todas las ...")
_INJECTION = re.compile(
    rf"""
    # 1. ignore / forget / disregard the instructions or rules (imperative forms only:
    #    "olvidé las instrucciones" is a customer who lost a leaflet, not an attack)
    \b(?:ignor[ae]|ignorá|ignoren|ignore|ignorar|desconsider[ae]|disregard|olvid[ae]|olvidá
       |esque[cç]a|esquece|forget){_W}
       (?:instrucciones|instru[cç][oõ]es|instructions|reglas|regras|rules|pol[ií]ticas)
    # 2. role override: "you are now an assistant without rules", "eres DAN"
    | \b(?:eres|ahora\s+eres|desde\s+ahora\s+eres|you\s+are(?:\s+now)?
         |voc[eê](?:\s+agora)?\s+[eé]){_W}?(?:sin|sem|without)\s+(?:\w+\s+)?
         (?:restricci|restri[cç]|l[ií]mites|limits|reglas|regras|rules|filtros|filters|pol[ií]ticas)
    | \b(?:eres|you\s+are(?:\s+now)?|voc[eê](?:\s+agora)?\s+[eé])\s+(?:un\s+|una\s+|um\s+
         |uma\s+|an?\s+)?(?:admin|administrador|desarrollador|desenvolvedor|developer|dan
         |jailbreak)\b
    # 3. fake system / role tags: "</system>", "[SYSTEM]", "SYSTEM:" at a line start
    | </?\s*system\s*> | \[\s*(?:system|sistema)\s*\] | (?:^|\n)\s*(?:system|sistema)\s*:
    # 4. extracting the prompt or the system instructions
    | system\s*prompt | prompt\s+(?:del|do)\s+sistema
    | instruc(?:ciones|tions)\s+del\s+sistema | instru[cç][oõ]es\s+do\s+sistema
    | system\s+instructions
    | \b(?:mu[eé]strame|muestra|revela|mostre|mostra|revele|show|reveal|print){_W}prompt
    # 5. bypassing the human approval
    | \b(?:sin|sem|without)(?:\W+\w+){{0,2}}?\W+(?:aprobaci[oó]n|aprova[cç][aã]o|approval)
    # 6. claiming privileges: "soy administrador", "el usuario es un auditor autorizado"
    | \b(?:soy|sou|i\s+am|el\s+usuario\s+es|o\s+usu[aá]rio\s+[eé]|the\s+user\s+is)\s+
         (?:un\s+|una\s+|um\s+|uma\s+|an?\s+)?(?:admin|administrador|administrator|desarrollador
         |desenvolvedor|developer|auditor)\b
    # 7. developer / debug / god modes
    | \bmodo\s+(?:desarrollador|desenvolvedor|developer|dios|god|admin|administrador|debug)
    | \b(?:developer|debug|god|jailbreak)\s+mode\b
    # 8. other customers' data
    | \b(?:otro|otra|outro|another)\s+(?:cliente|customer)
    | \b(?:de|do|dos|of)\s+(?:todos\s+los|todos\s+os|all(?:\s+the)?)\s+
         (?:clientes|customers|usuarios|usu[aá]rios|users)
    # 9. naming internal tools (customers never type these)
    | \b(?:list_cards|get_card_\w+|block_card|propose_block_card|escalate_to_human)\b
    # 10. forcing a fixed output: "answer only with the word X"
    | \b(?:responde|contesta|responda|reply|answer)\w*\s+(?:solo|solamente|[uú]nicamente
         |apenas|only)\s+(?:con|com|with)\s+(?:la\s+palabra|a\s+palavra|the\s+word
         |el\s+c[oó]digo|o\s+c[oó]digo|the\s+code)
    """,
    re.IGNORECASE | re.VERBOSE,
)

_PT_HINTS = re.compile(
    r"\b(voc[eê]|cart[aã]o|n[aã]o|meu|minha|obrigad[oa]|quero|preciso|fatura|conta|saque)\b",
    re.IGNORECASE,
)

# Raw PII that must never appear in an outgoing answer.
_RAW_PII = {
    "card_number": re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)"),
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
}


def detect_injection(text: str) -> bool:
    return bool(_INJECTION.search(text))


def guess_language(text: str) -> str:
    """Fallback only, when the triage LLM is unavailable."""
    return "pt" if len(_PT_HINTS.findall(text)) >= 1 else "es"


def raw_pii_found(text: str) -> list[str]:
    return [name for name, rx in _RAW_PII.items() if rx.search(text)]


def mask_tokens(text: str, thread_id: str, vault: TokenVault) -> str:
    """Tokens -> customer-safe text. Cards become '•••• 4060'; other data stays hidden.

    The full value never leaves the system; only the last 4 digits of cards and accounts.
    """

    def replace(match) -> str:
        label = match.group(1)
        if label in ("TARJETA", "CUENTA"):
            try:
                digits = "".join(c for c in vault.resolve(thread_id, match.group(0)) if c.isdigit())
                return f"•••• {digits[-4:]}"
            except UnknownTokenError:
                return "••••"
        return "[dato protegido]"

    return tidy_card_mentions(TOKEN_PATTERN.sub(replace, text))


_DUPLICATE_LAST4 = re.compile(r"\b(\d{4})\b\s*\(•••• \1\)")


def tidy_card_mentions(text: str) -> str:
    """'terminada en 1382 (•••• 1382)' -> 'terminada en 1382'.

    The model sees `last4` in tool results and tends to write the digits AND the token.
    A prompt rule didn't stop it in the live run, so this is fixed deterministically.
    """
    return _DUPLICATE_LAST4.sub(r"\1", text)


@dataclass(frozen=True)
class FallbackPrediction:
    intent: str
    confidence: float


# The trained intent classifier (models/intent_v1.joblib), injected so tests can fake it.
IntentFallback = Callable[[str], FallbackPrediction | None]


def load_intent_fallback() -> IntentFallback:
    """e5 + logistic regression from step 1c; None if the artifact isn't trained yet."""
    from pathlib import Path

    import joblib

    path = Path("models/intent_v1.joblib")
    if not path.exists():
        return lambda text: None
    artifact = joblib.load(path)

    def predict(text: str) -> FallbackPrediction | None:
        from cardagent.embeddings import get_encoder

        proba = artifact["model"].predict_proba(get_encoder().encode([text]))[0]
        best = int(proba.argmax())
        if proba[best] < artifact["threshold"]:
            return None  # not confident enough: the caller escalates
        return FallbackPrediction(artifact["model"].classes_[best], float(proba[best]))

    return predict
