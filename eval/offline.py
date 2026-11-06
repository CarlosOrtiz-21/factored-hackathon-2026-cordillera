"""Offline evaluation: FREE, no API calls. Runs on all golden cases.

    uv run python -m eval.offline

Measures the deterministic and locally-learned components on held-out cases:
1. fixtures: every case resolves to a real customer of its profile (data coverage);
2. PII guard: full card numbers in customer messages never reach the sanitized text;
3. injection regex (agents/guards.py): recall on direct injections, false-positive rate on
   everything else;
4. intent classifier (e5 + LR, models/intent_v1.joblib), the triage fallback: coverage
   above its confidence threshold and accuracy on what it accepts, by language;
5. language heuristic used when triage fails (guess_language).
Result: eval/results/offline.json (read by eval.report).
"""

import json
import re
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path

from cardagent.agents.guards import detect_injection, guess_language, load_intent_fallback
from cardagent.bank.db import BankDB
from cardagent.config import get_settings
from cardagent.pii.guard import PIIGuard
from cardagent.pii.vault import InMemoryTokenVault
from eval.cases import GOLDEN_VERSION, FixtureFactory, load_golden

OUT = Path("eval/results/offline.json")
CARD_NUMBER = re.compile(r"(?<!\d)\d{15,16}(?!\d)")


def rate(num: int, den: int) -> dict:
    return {"n": num, "of": den, "rate": round(num / den, 3) if den else None}


def main() -> None:
    settings = get_settings()
    cases = load_golden()
    workdir = Path(tempfile.mkdtemp(prefix="eval-offline-"))
    shutil.copyfile(settings.bank_db_path, workdir / "bank.db")
    db = BankDB(workdir / "bank.db")
    factory = FixtureFactory(db, settings.bank_as_of_date)
    texts = {c.id: factory.for_case(c).fill(c.message) for c in cases}  # raises if a fixture fails
    db.close()
    shutil.rmtree(workdir, ignore_errors=True)

    # 2. PII guard on messages that carry full card numbers
    guard = PIIGuard(InMemoryTokenVault())
    pii_cases = [c for c in cases if CARD_NUMBER.search(texts[c.id])]
    masked = 0
    for c in pii_cases:
        clean = guard.sanitize(f"offline-{c.id}", texts[c.id]).text
        masked += not any(n in clean for n in CARD_NUMBER.findall(texts[c.id]))

    # 3. injection regex
    direct = [c for c in cases if c.category == "injection_direct"]
    benign = [c for c in cases if not c.category.startswith("injection")]
    caught = sum(detect_injection(texts[c.id]) for c in direct)
    false_pos = [c.id for c in benign if detect_injection(texts[c.id])]

    # 4. intent classifier (triage fallback)
    predict = load_intent_fallback()
    by_lang: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    confusions = []
    for c in cases:
        if c.intent is None:
            continue
        pred = predict(texts[c.id])
        stats = by_lang[c.lang]
        stats["n"] += 1
        if pred is None:
            stats["abstained"] += 1  # below threshold: the real system escalates
            continue
        stats["accepted"] += 1
        if pred.intent == c.intent:
            stats["correct"] += 1
        else:
            confusions.append({"case": c.id, "expected": c.intent.value, "predicted": pred.intent})
    totals = {k: sum(s[k] for s in by_lang.values()) for k in ("n", "accepted", "correct")}

    # 5. language heuristic
    lang_cases = [c for c in cases if c.lang in ("es", "pt")]
    lang_ok = sum(guess_language(texts[c.id]) == c.lang for c in lang_cases)

    result = {
        "kind": "offline (no API calls)",
        "golden_version": GOLDEN_VERSION,
        "cases": len(cases),
        "portuguese_share": round(sum(c.lang == "pt" for c in cases) / len(cases), 3),
        "fixtures_resolved": rate(len(texts), len(cases)),
        "pii_card_numbers_masked": rate(masked, len(pii_cases)),
        "injection_regex": {
            "recall_direct": rate(caught, len(direct)),
            "false_positives": rate(len(false_pos), len(benign)),
            "false_positive_cases": false_pos,
        },
        "intent_fallback": {
            "coverage": rate(totals["accepted"], totals["n"]),
            "accuracy_when_accepted": rate(totals["correct"], totals["accepted"]),
            "by_language": {
                lang: {
                    "coverage": rate(s["accepted"], s["n"]),
                    "accuracy_when_accepted": rate(s["correct"], s["accepted"]),
                }
                for lang, s in sorted(by_lang.items())
            },
            "confusions": confusions,
        },
        "language_heuristic": rate(lang_ok, len(lang_cases)),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "intent_fallback"}, indent=2))
    fb = result["intent_fallback"]
    print("intent fallback:", fb["coverage"], fb["accuracy_when_accepted"])
    print(f"written to {OUT}")


if __name__ == "__main__":
    main()
