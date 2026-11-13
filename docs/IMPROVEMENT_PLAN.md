# Improvement plan

Written on 2026-11-13 from [RETROSPECTIVE.md](RETROSPECTIVE.md). It answers four questions for each gap against Faro (1st) and LedgerLens (2nd): what to improve, how, with what, and where we can go past what they did. It has three parts:

- **Part A** upgrades Lía, in this repository, in an order that pays off early. Each item names the files it touches and whether it costs API money (the user's rule: no paid run without an explicit go-ahead).
- **Part B** is the operating playbook for the next hackathon, day by day.
- **Part C** lists the places where neither winner went, and where a third attempt could lead.

Effort is in solo, part-time days (about 3 focused hours each). Costs are model spend only; the deployment stays free.

---

## Part A: upgrade Lía

Ordered by impact on what a judge sees, divided by effort. Items 1–4 change what the product *is*; 5–8 change how well we can *prove* it; 9–12 change how it *looks* to a reviewer.

### A1. Move the human to where the risk is (2 days, free)

**Gap.** Every lost-card block waits up to 10 minutes for a supervisor. Both winners confirm with the customer's own button and keep the person for ambiguity, conflicts, and disputes.

**How.** Two gates instead of one, both in code:

| Action | Gate | Who decides |
|---|---|---|
| Block the customer's own active card, reason lost/stolen/suspicious | Customer confirmation control (a button; typed "sí" never counts) | The customer |
| Same, when `risk_score > 0.7`, the card is already in a dispute, or the target check found a conflict | Customer confirmation **and** supervisor approval | Both |
| Unblock, dispute intake, anything irreversible or outside policy | Structured handoff | A person |

- `critic` keeps computing `requires_human_approval`, but the rule becomes `is_write and (risk > threshold or conflict)`; a plain write goes to a new `confirm` node.
- `confirm` writes a confirmation record (`confirmations` table: id, thread, card token, reason, expires_at, consumed_at) and emits a new SSE event `confirm_required`. `block_card` consumes the record in one transaction; a record that is missing, expired, or for another card refuses the write. This is Faro's design: the flag the tool checks comes from the click, never from the model.
- Frontend: a confirmation card with the masked card and the reason, two buttons; the composer stays disabled until a click. The auditor panel keeps the supervisor queue for the risky subset.
- Keep the 10-minute TTL on confirmations; a lapsed one is reported as "not blocked", in code.

**Beyond the winners.** Keep the supervisor path, which they don't have, for the risky subset, and show in the audit trail *why* a case needed two gates. A judge sees a graduated policy, not a blanket one.

**Files.** `graph/build.py` (new node, routing), `bank/tools.py` (`block_card` consumes the record), `bank/db.py` (table), `api/runner.py` + `api/main.py` (event + `POST /api/confirm`), `frontend/src/components/chat/*`, `agents/templates.py`, tests in `test_graph.py`, `test_bank.py`, `test_api.py`.

### A2. Let the model do less: placeholders and a reply check (3 days, free to build; re-eval paid)

**Gap.** Sonnet writes the whole answer and a second Sonnet reviews it: two expensive calls, 7.4 s median, and a critic that only sometimes catches an invented figure. Faro's model writes words around `{card.expiration}` and code fills every fact; a reply check rejects any digit the model wasn't allowed to write.

**How.**
- `executor` returns a structured reply: text with named placeholders drawn from `read_results` (`{card.last4}`, `{tx[0].amount}`, `{card.status}`), never values. Pydantic validates placeholder names against the facts actually read in that turn.
- `respond` fills them in the customer's language and number format (we already have `templates.render` and `mask_tokens`).
- A deterministic **reply check** before sending: no digits outside filled placeholders, no `<TARJETA_n>` tokens, no internal flags, no status the policy withholds. A failing reply is replaced by a fixed one built from the same facts, and the fallback is counted in `usage`/audit.
- Downgrade the executor and RAG to Haiku 4.5 once placeholders carry the facts; keep the critic only for policy answers (RAG groundedness), where wording carries meaning. Measure before and after (A6).

**Beyond the winners.** Faro's check is a blacklist of digits; ours can be a whitelist derived from the tool outputs of the turn (every number in the reply must appear in a tool result or a template). Add it as a `safety_violation` so the eval counts it.

**Files.** `schemas/agents.py` (executor output model), `agents/prompts.py` (v1.3), `graph/build.py` (`respond`, check), `agents/guards.py` (`reply_check`), `llm/profiles.py`, tests.

### A3. Journeys instead of capabilities (2 days, free)

**Gap.** We answer "is my card active?"; LedgerLens explains *why a payment was declined*, converts a foreign charge, and notices when the bank's records contradict each other.

**How.** Add three read journeys the data already supports (`DEMAND_ANALYSIS.md`, `DATA_QUALITY.md`):
- **Declined transaction:** `explain_decline(tx)` reads `response_code` and maps it to the ISO 8583 meaning; a missing or unlisted code is reported as "not recorded", never guessed.
- **Foreign charge:** amount in the card's currency at that day's rate from `daily_exchange_rates` (already in the warehouse).
- **Conflicting records:** an active card past `expiration_date` (half of them in this data) is reported as a conflict, side by side, and handed off; never reconciled.
- Session context at login: cards, last 72 h of transactions, open complaints, so the welcome can say "I see a declined payment yesterday; is that why you're writing?" (LedgerLens's `classify_call_type`). Our intent classifier can score the three likeliest reasons from the context, with no extra model call.

**Files.** `bank/tools.py` (two tools), `pipelines/etl/export.py` (rates and `response_code` into `bank.db`), `corpus/policies/*` (declines section, both languages), `agents/templates.py`, golden cases.

### A4. Handoff facts tied to tool calls (1 day, free)

**Gap.** Our `HandoffPayload` lists facts; Faro ties each to the tool call that read it, keeps the customer's statements apart, and adds the flags the model never saw.

**How.** `verified_facts` becomes `[{fact, tool, call_id, ts}]` drawn from `read_results` with the audit event id; `customer_statements` holds the sanitized quotes; `file_handoff` (code) adds `is_fraud` on the disputed transaction and sets `priority: urgent` when a block was cancelled on an unrecognized charge. The auditor panel renders fact → call.

**Files.** `schemas/records.py`, `graph/build.py` (`escalate`), `frontend/src/components/auditor/ThreadInspector.tsx`, tests.

### A5. The rubric as requirement IDs with a traceability test (1 day, free)

**Gap.** Faro restated every judged requirement as a stable ID and cites them everywhere; we summarized the rules once.

**How.** `docs/requirements.md` with IDs per dimension (SCP, AI, CTL, SEC, DML, EVL, OPS, DSN), one line each, taken from the organizers' page and the problem statement. Then cite them in ARCHITECTURE.md, the policy corpus README, the eval report, and test docstrings.

**Beyond the winners.** A CI test, `tests/test_traceability.py`: every ID in `requirements.md` must be cited by at least one test or one doc, and every citation must resolve to an existing ID. Faro cites by hand; we make the matrix a failing test.

### A6. An evaluation a judge would trust (5 days; paid runs, see costs)

**Gap.** 33 single-turn cases, one run, hand-written expectations. Faro: 608 multi-turn cases, three runs, an oracle independent of the tools, a validated judge, pre-registered studies.

**How, in order:**
1. **An oracle.** `eval/oracle.py` applies the policy to the frozen `bank.db` and returns the expected outcome class and facts for a case, reading the database directly and sharing no code with `bank/tools.py`. Disagreements between oracle and golden expectations become a triage log (`eval/results/disagreements.md`), as Faro's.
2. **Multi-turn cases.** A scripted customer that reacts to decision classes (asked which card → answers; confirm control → presses yes/no; handoff → stops), never to prose. Case = customer profile + situation + answer script. Reuse `FixtureFactory`.
3. **Families and a real split.** Write seeds per request family in ES and PT; generate paraphrases with a **different model family** than the system's (Gemini or GPT, or a local model) to avoid the same-family flattery Faro flagged; hold out a third of families and a fifth of customers; add a test that no held-out message or customer appears in any development artifact.
4. **Three runs**, reported as pass^3 with intervals; CI plays the regression subset with `FakeLLM` for free.
5. **The judge**, only after a blind hand-graded sample (we have `review_sample.csv` tooling from step 1c): fix the bar first (90% agreement, κ ≥ 0.6, ≥ 10 deserved "no"s per question); use only questions that pass; use a judge from another provider.
6. **The model grid** Faro designed and never ran: {Haiku-only, mixed, Sonnet-only} × {prompt v1.2, v1.3} on the selection set, ranked on unsafe outcomes, then correctness, then cost.
7. **Pre-registration.** A study file with hypotheses and the set manifest hash, committed before the run; a test that the manifest hash in the report matches the committed one.

**Costs (estimate, at today's measured per-case cost).** Build: free. Selection set of ~150 cases × 1 run ≈ US$3 on the current mix, ≈ US$1.5 after A2. Held-out 600 × 3 runs ≈ US$30 now, ≈ US$15 after A2. Judge on a sample of 150 replies ≈ US$2. The grid ≈ US$10. Each run needs the user's go-ahead and `--max-usd`.

### A7. Data Analytics: a metrics page built from the audit trail (2 days, free)

**Gap.** The rubric has a Data Analytics dimension; we have a markdown demand analysis. Faro designed an "AI team page" and didn't build it; LedgerLens has CloudWatch dashboards a judge can't open without AWS access.

**How.** A static page at `/metrics/` in the frontend, built at deploy time from `eval/results/*` and the audit trail of the demo: resolution, containment, escalation reasons, unsafe outcomes, latency, cost per resolution, by language and segment, plus the demand charts from `DEMAND_ANALYSIS.md`. Use the `dataviz` skill for the charts. A judge sees the numbers without credentials.

**Beyond the winners.** Add a **fairness view**: outcome rates by segment and country with intervals, and an invariance test (same case, different segment → same outcome class) run in CI on `FakeLLM`. Both winners mention fairness; neither tests it.

### A8. Engineering hygiene as evidence (2 days, free)

**Gap.** Faro's repo reads like a hiring signal: Terraform, release gates, CHANGELOG, security scanning, SECURITY.md, ADRs.

**How.**
- `docs/adr/` with MADR-style records for the decisions ARCHITECTURE.md already explains (models, HITL design, PII vault, static frontend, free deploy, eval design). Backfill six, then one per future decision.
- GitHub Actions: `quality-gates.yml` (ruff, pytest, frontend typecheck/build, `eval.offline`), Dependabot, CodeQL (free on public repos), `gitleaks` pre-commit; a `SECURITY.md`.
- `CHANGELOG.md` with Keep a Changelog, a tag per deploy.
- Infra as code for the free stack: Terraform with the Google provider for Cloud Run, Secret Manager bindings, and the budget alert; the GitHub Pages workflow already is IaC.
- Pin the toolchain with `mise` (Python, Node, uv, terraform).

### A9. A judge's path with no credentials (1 day, free)

**Gap.** Faro has "Entrar como invitado"; LedgerLens has five scripted judge personas. We mail codes.

**How.** `POST /api/guest` creates a session for one of 12 pre-chosen demo customers (4 per country, each able to play the three paths), labeled `visitor` so its cases never reach a real queue; a per-day guest cap and the existing spend cap protect the budget. The login page gets a guest tab and a **scripted persona list**: what to say, what must happen, for each of the three paths and both languages.

### A10. README that leads with the business (half a day, free)

**Gap.** Ours opens with what Lía is; LedgerLens opens with contact-center cost, NPS, FCR, with dataset baselines.

**How.** First screen: the problem in numbers from `DEMAND_ANALYSIS.md` (35% of contacts and products on cards, FCR and escalation per reason, handle times), then the result (resolution, unsafe outcomes, cost per resolution), then an ROI **labeled as a projection** with the cost-per-contact parameter made explicit, as Faro does. Then "two things the table doesn't show." Then architecture. Limitations get a top-level section, not a buried report.

### A11. Observability (1 day, free tier)

Re-add LangSmith (free tier) or OpenTelemetry traces to Cloud Trace: one span per node, per model call, per tool call; counts and IDs only, never message text. Alarms on fallback share, errors, and daily spend already exist as a cap; add Cloud Monitoring alerts on 5xx and latency p95.

### A12. Finish the submission artifacts (1 day, free)

Music and the Markdown fix in the video; the deck's slide 2 rewritten around the business numbers of A10; a 4-minute walkthrough script for judges.

### Order and budget

| Week | Items | Model spend |
|---|---|---|
| 1 | A1, A4, A5, A10 | 0 |
| 2 | A2, A3 | 0 |
| 3 | A6 steps 1–4 (build), A8 | ≈ US$3 (selection set, once) |
| 4 | A6 steps 5–7, A7, A9 | ≈ US$25–45 depending on A2 |
| 5 | A11, A12, re-record demo, final report | 0 |

---

## Part B: the playbook for the next hackathon

Ten days, solo or in a pair. Built from the winners' commit curves (Faro: 595 commits, peak on day 8; LedgerLens: 248, peak on day 7) and from our failure (two days of work, then silence).

| Day | Deliverable at the end of the day | Why |
|---|---|---|
| 0 (before start) | Toolchain pinned, CI skeleton, deploy target chosen and tested with a hello-world, ADR template, requirements file template | Nothing infrastructural on the clock |
| 1 | `requirements.md` with IDs from the brief; data profiled (`make analysis`); **workflow-selection rule committed before the numbers** | Lesson 2 and 3 |
| 2 | Workflow chosen in an ADR; policy with rule IDs; **a deployed link that answers one question**; README with the problem in numbers; video outline | Lesson 1: ship from day 2 |
| 3 | Tools over a frozen snapshot, permissions in code, the one write behind a confirmation record | The golden rule, from the start |
| 4 | The graph with model steps (route, extract, placeholders), reply check, handoff schema; CI regression set with a fake model | Lesson 4 |
| 5 | Frontend: chat + confirmation control + console; guest entry; judge personas | Lesson 7 |
| 6 | Eval harness: oracle, scripted customer, families with a held-out split, leakage tests; **study pre-registered** | Lesson 6 |
| 7 | Selection run; fix; freeze. Metrics page from results. Slides draft | |
| 8 | Held-out run ×3 (budget approved), judge validation on a blind sample, report with failures and limitations | |
| 9 | Video recorded, deck final, README's "two things the table doesn't show", CHANGELOG, tag | |
| 10 | Buffer: nothing new; verify the link, the guest entry, the personas; submit by noon | A full day of slack |

Rules that would have changed our outcome:
- **A daily end-of-day commit that is submittable.** If the day ends and the link is broken, fix the link before anything else.
- **Pre-register, then measure.** Rules and hypotheses in git before the numbers.
- **One write, one journey, three paths, both languages, working end to end before any second feature.**
- **Budget as a feature.** Caps and alarms from day 3, so a public link can stay public.
- **Write the limitations as you go**, not at the end.

---

## Part C: past the winners

Where neither Faro nor LedgerLens went, each feasible on our stack and free or near-free.

1. **PII tokenization + placeholders together.** Faro masks card numbers; we tokenize every entity before the model and would also fill facts in code. The model sees neither the person nor the numbers, and the reply carries only what a tool read. No other entry had both.
2. **A traceability matrix that fails CI** (A5). Requirement IDs cited by hand can rot; a test can't.
3. **Fairness as an invariance test** (A7), not a table: the same situation across segments and countries must yield the same outcome class, checked on every CI run with the fake model and on every live run with real ones.
4. **Metamorphic language tests.** Each held-out case exists in ES and PT with the same expected outcome; a test asserts the outcome classes match pairwise. Faro reports per-language rates; a pairwise check is sharper and finds the "answered in the other language" class directly.
5. **A judge from another provider, and label quality measured.** Faro admitted both gaps (same-family judge, relabel sheet never graded). A 50-message blind relabel takes an hour; the user's `review_sample.csv` flow already exists.
6. **The model grid actually run** (A6.6). Faro designed it and dropped it; LedgerLens ran it on 10 cases. Running it on 150 with pass^3 is the strongest "trade-offs made explicit" evidence available.
7. **Graduated oversight** (A1): customer confirmation for protective actions, two gates for risky ones, a person for the rest, with the reason recorded. The winners have one gate each.
8. **A free, capped public deployment** with a guest entry. Both winners run on a full AWS account; a judge who can fork and deploy for free is a reproducibility claim no one else made.
9. **An offline evaluation anyone can rerun.** Faro's dataset can no longer be downloaded, so forks can't reproduce its numbers. Our `FakeLLM` regression set and a synthetic bank fixture already run with no data and no key; publishing a redacted, synthetic `bank.db` slice (not derived from the organizers' data) would make the live subset reproducible too.
10. **Teaching-grade code.** The explicit ReAct loop and the hand-built graph were a learning goal; keep them, but document them as a trade-off in an ADR ("explicit over prebuilt: why, and what it costs"), so a judge reads intent, not naivety.
