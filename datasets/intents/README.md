# Card-support intents dataset (v1)

**Provenance: TEAM-GENERATED, AI-assisted.** The utterances were written during development with an AI coding assistant (Claude) to match the system's intent schema. They are **not** real customer data and do not come from the LATAM Bank dataset. The dataset's own labels were unusable: `contact_reason` has 6 coarse values, `detected_intents` is constant, and the transcripts are 42 templates (see `docs/DATA_QUALITY.md`).

| | |
|---|---|
| File | `card_intents_v1.csv` (`group, intent, lang, text`) |
| Size | 504 utterances in 84 paraphrase groups (14 per intent) |
| Intents | `card_status`, `card_transactions`, `card_block`, `card_policy`, `human_request`, `out_of_scope` (84 each) |
| Languages | Spanish 336 (neutral, Mexican, Colombian, and Argentine variants), Brazilian Portuguese 168 |

## Design
- **Paraphrase groups:** each group is one underlying request, written 4 ways in Spanish and 2 in Portuguese. Splits are **by group**, so a paraphrase or translation of a test item never appears in training. This is the leakage control.
- **Sanitized input:** some utterances contain PII tokens (`<TARJETA_1>`, `<CUENTA_1>`), because in production the classifier sees text after the PII guard.
- **Hard pairs on purpose:**
  - "me robaron la tarjeta" (`card_block`) vs. "¿qué hago si me roban la tarjeta?" (`card_policy`)
  - "¿mi tarjeta está bloqueada?" (`card_status`) vs. "bloquea mi tarjeta" (`card_block`)
  - "¿cómo desbloqueo mi tarjeta?" (`card_policy`) vs. "se me bloqueó el usuario de la app" (`out_of_scope`)
- **Unrecognized charges** ("no reconozco un cargo") are labeled `card_transactions`: the system first verifies the transaction, then escalates with a structured handoff.
- Typos, missing accents, and regional slang are included on purpose.

## Labeling guideline
- `card_policy`: how *cards* work in general. This covers fees, replacement cost and delivery time, limits, interest and minimum-payment rules, travel use, security practices around cards, and how to unblock or dispute.
- `card_status` / `card_transactions`: questions about *this customer's* card or its movements.
- `out_of_scope`: other products (loans, accounts, investments, insurance claims), account administration (address, app password), and non-banking topics.

## Label validation
`review_sample.csv` holds a blind, stratified random sample of 60 utterances (10 per intent), labeled by a human reviewer who did not see our labels.

**Result: 86.7% agreement, Cohen's kappa 0.84.** All 8 disagreements are on the `card_policy` / `out_of_scope` boundary. That boundary wasn't written down before the review; the guideline above now makes it explicit. Our labels were kept. Details are in `docs/models/INTENT_MODEL.md`. Re-run the check with `pipelines/models/intents.py --review`.

## Known limitations
- Written by the same party that designed the intents, so it probably makes the task look easier than real traffic would.
- Brazilian Portuguese only; no European Portuguese.
- No code-switching and no voice-transcription noise.
