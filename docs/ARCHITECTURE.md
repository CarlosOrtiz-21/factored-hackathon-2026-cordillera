# Architecture: AI-first card-support assistant with human-in-the-loop

> Phase 0 decision record. Status: **approved 2026-09-25**. The original spec is [PROJECT_PROMPT.md](PROJECT_PROMPT.md) (Spanish); deviations from it are listed at the end with their justification.

## 0. Brand (fictional)

The assistant is **Lía**, of **Banco Cordillera**, a **fictional** bank invented for the hackathon. Any resemblance to a real bank is unintended, and its phone line (`#2222`) and hours are fictional too. The policy corpus is the fictional bank's synthetic policies.

**Voice:**
- Warm and clear, with minimal emojis and none around loss, theft, or fraud.
- Every reply follows the same structure: acknowledge, answer with facts, offer a next step.
- Money, dates, and cards are formatted for each language.
- The rules are in `src/cardagent/agents/style.py`.

## 1. Scope: one focused workflow, card support

| Intent | Kind | Example |
|---|---|---|
| `card_status` | read | "¿Mi tarjeta está activa?" |
| `card_transactions` | read | "Muéstrame los últimos movimientos de mi tarjeta de crédito" |
| `card_block` | **write → HITL** | "Perdí mi tarjeta, bloquéala" |
| `card_policy` | RAG | "¿Cuánto cuesta la reposición?" |
| `human_request` | escalation | "Quiero hablar con un asesor" |
| `out_of_scope` | refusal | "Dame un crédito hipotecario" |

- There are **no money movements**, so `transfer` from the original spec is out of scope.
- **Unrecognized charges** are handled this way: the system verifies the transaction exists and belongs to the session's customer, then **escalates** with a structured handoff. It never opens a dispute on its own. This is the "human required" path the challenge asks for.
- **Why card support:** the ETL step (1b) quantifies the share of card-related `contact_reason`/`reason_category` in `call_center_interactions` and `complaints`, along with first-contact resolution (FCR) and escalation rates per reason. That analysis is the data-backed justification.

## 2. Repository layout

```text
.
├── pyproject.toml / uv.lock         # uv: backend + pipelines + eval
├── src/cardagent/
│   ├── config.py                    # pydantic-settings, reads .env
│   ├── schemas/                     # state, agent outputs, tool args, API, handoff payload
│   ├── llm/                         # Anthropic client + structured-output resilience chain
│   ├── pii/                         # Presidio recognizers, token vault, guard
│   ├── bank/                        # SQLite mock bank, sessions, tool registry, read/write tools
│   ├── rag/                         # policy ingestion + hybrid retriever
│   ├── agents/                      # triage, executor, policy_rag, critic (versioned prompts)
│   ├── graph/                       # LangGraph build, nodes, routing, HITL
│   ├── audit/                       # append-only audit events (tokens only)
│   └── api/                         # FastAPI: SSE chat, approve, state, mock auth
├── pipelines/etl/                   # S3 → bronze → silver → gold, contracts, quality report
├── pipelines/intent_model/          # learned intent classifier (train/evaluate)
├── corpus/policies/{es,pt}/         # SYNTHETIC card policies, clearly labeled
├── eval/                            # golden cases, baseline system, runner, reports
├── tests/                           # one test module per roadmap step
├── frontend/                        # Next.js: chat + auditor panel (minimal)
└── docs/
```

The data lives outside git: `data/raw/`, `data/warehouse.duckdb`, and `data/bank.db`.

## 3. Graph state

```text
# identity: set by the API from the authenticated session, never by an LLM
thread_id, session_id, customer_id, requester_id
# input
messages (add_messages; sanitized text only), sanitized_input, pii_token_refs: list[str]
# triage
language: es|pt|other, intent, urgency, injection_suspected, triage_source: llm|classifier_fallback
# execution
react_steps: list[{thought, action, observation}], react_iterations, read_results
proposed_action: {tool, args (tokens), idempotency_key} | None
# RAG
rag_context, citations, answerable
# critique
draft_response, critic_feedback, risk_score, safety_violations, verdict, retries
# HITL
requires_human_approval, human_decision: {decision, approver_id, comment, ts} | None
action_result: {executed, verified, observed_state} | None
# output
escalation: HandoffPayload | None, final_response
# traceability & metrics
audit_trail: Annotated[list[AuditEvent], operator.add]   # append-only
usage: list[{node, model, input_tokens, output_tokens, latency_ms}]
```

Fields added to the original draft, and why:

| Field | Reason |
|---|---|
| `session_id`, `customer_id`, `requester_id` | Tools must enforce that the session owns the resource. These values come from the session and are never inferred by a model. |
| `language` | Reply in the customer's language (ES or PT) and report metrics per language. |
| `triage_source` | Records when the learned classifier replaced a failed LLM call. |
| `react_steps`, `react_iterations` | Every Thought, Action, and Observation step is traced, and the 5-iteration cap is enforced. |
| `action_result.verified` | Only verified outcomes are reported: after `block_card`, the card status is read back. |
| `escalation` | The structured handoff: request, verified facts, actions taken, evidence, and open questions. |
| `usage` | Needed for p50/p95 latency and cost per case. |

**Invariants:**
- `pii_token_refs` holds references only. Real values live in the vault.
- Tool outputs pass through the vault before reaching any LLM, so the model sees tokens and last-4 digits only.

## 4. Graph and fallbacks

```text
API: validate session (expired/invalid → 401, never enters the graph) → Presidio sanitizes → graph

triage ──(LLM fails twice)──> classifier_fallback (TF-IDF) ──(low confidence)──> escalate
  ├─ injection_suspected ───────────────────────────> reject
  ├─ out_of_scope ──────────────────────────────────> reject
  ├─ human_request ─────────────────────────────────> escalate
  ├─ card_status | card_transactions | card_block ──> executor
  └─ card_policy ───────────────────────────────────> policy_rag

executor   (explicit ReAct: reason ⇄ read_tool, max 5 iterations;
            tool failure → 1 retry → escalate)                          ──> critic
policy_rag (answerable=false → abstention draft + offer of a human)     ──> critic

critic     (its own output invalid → verdict=block, i.e. fail closed)
  ├─ revise and retries < 2 ──> back to executor / policy_rag with feedback
  ├─ block or retries ≥ 2 ──> escalate
  ├─ requires_human_approval (CODE RULE) ──> human_approval
  └─ approve ──> respond

human_approval (interrupt(); approver ≠ requester; TTL 10 min → cancelled)
  ├─ approved ──> tool_executor (deterministic, idempotency key)
  │                 └─> verify (re-read state) ──ok──> respond
  │                                            └─mismatch──> escalate ("action not verified")
  └─ rejected / expired ──> respond (reports the cancellation)

reject   = safe refusal (injection, out of scope); discloses no customer data
escalate = builds the HandoffPayload → respond ("an advisor will take your case")
respond  = writes the answer in the customer's language; re-identifies only data the customer may see
```

**Golden rule:** no side-effecting tool runs before `human_approval`. The executor has **read tools only** and emits writes as a `proposed_action`. A deterministic node with no LLM (`tool_executor`) executes the write after approval. The gate is code:

```python
requires_human_approval = is_write(proposed_action) or risk_score > 0.7
```

The critic's `risk_score` is an input signal; the rule is the barrier.

## 5. Decisions

| Topic | Decision | Rationale |
|---|---|---|
| LLM | **Revised 2026-09-26: mixed models.** Triage uses `claude-haiku-4-5` with `temperature=0.0`. Executor and policy_rag use `claude-sonnet-5` with `effort=medium`; the critic uses `claude-sonnet-5` with `effort=low`. Configurable per agent; see `src/cardagent/llm/profiles.py`. | Opus 5 was overkill. Triage is a short JSON classification that runs on every message, so latency and cost dominate; Haiku accepts temperature, and the e5 classifier backs it up. Tool use, grounded citations, and critique need stronger reasoning, so they use Sonnet. The critic should not be weaker than what it reviews, although the HITL barrier stays the code rule. Estimated ~$0.035 per turn vs. ~$0.09 on Opus 5, to be measured in step 6. Sonnet 5 rejects `temperature` and Haiku rejects `effort`: each agent declares both, the model profile picks, and invalid configs fail at startup. |
| `max_tokens` | Haiku (no default thinking): the spec's values, i.e. triage 256, executor 1024, policy_rag 1536, critic 512. Sonnet 5 (adaptive thinking on): triage and critic 2048, executor and policy_rag 4096. | Thinking tokens share the output budget. |
| SDK | Official `anthropic` SDK called directly inside LangGraph nodes, using `client.messages.parse()` with Pydantic models | This removes a layer and makes each call explicit, which helps learning. LangGraph does not require the LangChain model wrappers. Structured outputs and tool calling are native to the SDK. |
| Structured-output resilience | Native structured output → `json_repair.repair_json()` → Pydantic validation → safe response plus a logged error | Unvalidated data never flows forward. |
| Refusals | Check `stop_reason == "refusal"` and enable server-side fallbacks (`fallbacks: "default"`); a remaining refusal is treated as a node failure and takes the safe path | A model refusal must never leave the flow in an undefined state. |
| PII | Presidio + Spanish spaCy model + custom recognizers: CC, NIT, CURP, DNI, cards (Luhn-validated), accounts, mobile numbers, email. Reversible tokens (`<TARJETA_1>`) stored in a vault keyed by `thread_id` | The LLM never sees raw PII. Only tools and `tool_executor` resolve tokens. |
| Vault | In-memory store behind an interface | This is enough for a solo demo. Redis is the documented production step. |
| Data store | DuckDB for the ETL (S3 → bronze/silver/gold) → SQLite `bank.db` holding a demo-customer subset | DuckDB handles 19M rows on a laptop. SQLite gives transactional writes and idempotency for the mock bank. |
| Retrieval | Embedded Chroma + local `intfloat/multilingual-e5-small` embeddings + BM25, combined as hybrid search | Anthropic has no embeddings endpoint. The local model is multilingual (ES/PT), free, and keeps data in-house. The corpus is small (~10 documents). |
| Policy corpus | Synthetic card policies (ES, with PT translations of the key documents), each labeled **SYNTHETIC** | The challenge allows a clearly labeled synthetic policy service. |
| Checkpointer | `langgraph-checkpoint-sqlite` for development; Postgres (`langgraph-checkpoint-postgres`) for production, which only swaps the saver | Zero infrastructure now and a one-line migration later. |
| HITL mechanism | Dynamic `interrupt()` inside the `human_approval` node, resumed with `Command(resume=decision)` | `interrupt_before` always pauses before a node and is fixed at compile time. `interrupt()` pauses from inside the node only when code decides to, and it receives the decision payload. It is the currently recommended pattern; we verify it against the installed LangGraph version. |
| Observability | LangSmith (free tier) for graph traces, plus our own `audit_trail` as the audit artifact | Tracing is quick to set up. The audit trail holds sources, rules, and execution records, never chain-of-thought. Only tokens leave the machine. |
| Auth | Mock customer sessions with expiry, checked in the API and in every tool; mock auditor role for approvals, and the approver cannot be the requester | A customer_id or document number alone never proves identity. The approver rule enforces segregation of duties. |
| Frontend | Minimal Next.js: a chat page and an auditor panel, streaming via `@microsoft/fetch-event-source` | Required by the spec, but kept small because this is a solo, part-time build. |
| Deployment | Backend Docker image on Render or Railway; frontend on Vercel | This is the fastest path to the required public link. Final choice at step 7. |

## 6. Learned components and evaluation

> **Revised after profiling (step 1b), 2026-09-26.** The dataset has no usable intent labels:
> `contact_reason` equals `reason_category` (6 coarse values), `detected_intents` is always
> `consulta_general`, and the transcripts are 42 templates. See [DATA_QUALITY.md](DATA_QUALITY.md).
> Decision: build **two** learned components (step 1c).

**1. Intent classifier (primary):**
- Labels: a **team-generated** set of ~600 ES/PT card-support utterances, labeled with our intents. It is clearly marked as team-generated, and a hand-reviewed sample validates the labels.
- Candidates: keyword rules (baseline) vs. TF-IDF + logistic regression vs. multilingual-e5 embeddings + logistic regression vs. zero-shot LLM triage.
- Split **by paraphrase group**: all variants of the same underlying request land in the same split, so the model can't pass the test by memorizing near-duplicates.
- Deployed as the triage fallback when the LLM fails, which is also its role in the Portuguese coverage story.

**2. Suspicious-transaction model (secondary):**
- Labels: real `transactions.is_fraud` on ~1.55M card transactions (0.1% positive).
- `fraud_score` is **excluded** as a feature: it separates the labels almost perfectly (mean 15 vs. 49), which suggests it was derived from the label and would leak.
- Temporal split. Compared against a rules baseline, with metrics suited to heavy imbalance (PR-AUC, recall at a fixed alert rate).
- Used as a read-only signal in `card_transactions`: it flags charges to confirm with the customer. It never blocks anything by itself.

**Results (step 1c, 2026-09-26):**
- **Intent classifier:** see [INTENT_MODEL.md](models/INTENT_MODEL.md).
  - Selected **multilingual-e5-small + logistic regression**, chosen by grouped CV (macro-F1 0.76 vs. 0.66 for TF-IDF).
  - Held-out macro-F1: **0.838** (ES 0.820, PT 0.873), vs. 0.712 for keyword rules and 0.048 for the majority class.
  - Fallback threshold 0.67: routes ~62% of cases at ~99% accuracy and escalates the rest.
  - Label validation: blind human review of 60 utterances, 86.7% agreement, Cohen's kappa 0.84. All disagreements are on the `card_policy` / `out_of_scope` boundary.
- **Suspicious-transaction model: NOT DEPLOYED (negative result).** See [FRAUD_MODEL.md](models/FRAUD_MODEL.md).
  - Every legitimate feature set, and the rules baseline, is indistinguishable from random (test ROC-AUC ~0.51, PR-AUC ≈ prevalence).
  - The excluded `fraud_score` alone reaches PR-AUC 0.56, so in this synthetic data `is_fraud` is only recoverable through the leaky column.
  - Consequence: the assistant shows no model-based fraud flag. Unrecognized charges go through the customer's report plus escalation.

**Golden set (~120 cases, ~30% Portuguese, team-generated and labeled):**

| Category | Cases |
|---|---|
| Normal: status, transactions, block | 30 |
| Policy, answerable from the corpus | 20 |
| Policy, not answerable from the corpus | 10 |
| Ambiguous | 15 |
| Prompt injection: 10 direct, 5 indirect via a poisoned policy chunk | 15 |
| Authentication: expired session, another customer's card | 10 |
| Tool failures (injected) | 10 |
| Out of scope | 10 |

**Customer-experience evaluation (added 2026-09-26):**
- Deterministic checks:
  - the reply is in the customer's language;
  - no emojis in loss, theft, or fraud flows;
  - every card is masked;
  - reply length.
- An LLM-judge rubric (Claude Haiku) scores empathy, clarity, actionability, and tone fit for urgency, each from 1 to 5. The judge is **validated against human ratings on ~30 replies** before its scores are reported, and results are broken down by language.
- Before/after wording comparison of prompts v1.0 and v1.1: `scripts/compare_voice.py` writes `docs/voice_comparison.md`.

**Baseline system:** a single LLM with all tools, including `block_card`, and no critic or HITL. It runs on the same held-out cases.

**Model ablation:** the same golden set runs with the mixed config and with all agents on `claude-haiku-4-5`. Quality, unsafe outcomes, p50/p95 latency, and cost are reported side by side to justify the model choice with data.

**Metrics:**
- PII recall.
- Intent accuracy.
- Share of writes that went through HITL (**target 100%**).
- RAG groundedness and correct-abstention rate.
- Injection block rate.
- Safe automated resolution rate (plus the share of cases where automation was attempted).
- Containment.
- Missed and unnecessary escalations.
- Unsafe outcomes, reported as n/N.
- p50/p95 latency.
- Cost per attempted case and per successful resolution.

All metrics are broken down by language and customer segment, with 3 runs to measure variance. Model and prompt versions are recorded, and offline results are labeled as offline.

**Unit tests:** use a fake LLM client, so they are free and deterministic. Real model calls run only during evaluation.

## 7. Roadmap (solo, part-time; submission 2026-10-05)

| Date | Step | Done when |
|---|---|---|
| Sep 25–26 | 0 this document · 1 schemas, config, Presidio + vault | PII tests (CC, NIT, CURP, DNI, Luhn cards, accounts) pass |
| Sep 26 | 1b ETL + demand analysis ✅ | Quality report exists, re-runs are idempotent, late-arrival fixture test passes |
| Sep 26 | 1c learned components: intent classifier + suspicious-transaction model ✅ | Metrics vs. baselines recorded |
| Sep 26 | 2 mock bank API + sessions ✅ | Tests pass for allowlist, idempotency, cross-customer access, expired session |
| Sep 26 | 3 agents + graph + HITL ✅ (FakeLLM tests; live run pending) | Test shows every `block_card` interrupts before execution and is verified after |
| Sep 26 | 4 FastAPI SSE + approve + state ✅ (live: approve/reject/expire) | Full flow via curl: approve, reject, expire |
| Oct 3 | 5 minimal frontend ✅ (builds; smoke-tested; browser run pending) | End-to-end flow works in the browser |
| Oct 4 | 6 golden set + evaluation ✅ (offline 120 cases; live 33 cases, US$0.89) | `docs/EVAL_REPORT.md` written |
| Oct 5 | 7 deployment + slides + video | Public link live, submission sent |

**Cut order if behind schedule:** frontend polish → PT policy translations → LangSmith → reduce from 3 runs to 1.

## 8. Data pipeline (step 1b, verified)

- **Source layout:** UTF-8 CSV files with a BOM. Dimensions are single snapshot files; facts are hive-partitioned daily (`year=/month=/day=`). We pull 8 tables (~1.2 GB) and skip `digital_events` (3.7 GB) and the campaign tables.
- **Credentials:** read with `boto3` from `.env`; the AWS CLI is not needed.
- **Layers:**
  - bronze: raw + lineage columns;
  - silver: typed, normalized, deduplicated by key with the latest version kept, orphans flagged, contract violations in `quarantine_*`;
  - gold: `gold_card_products`, `gold_card_transactions`, `gold_interactions`, `gold_complaints`;
  - export: `data/bank.db`, with 2,000 demo customers chosen deterministically.
- **Incremental and idempotent by file:** `etl_processed_files` records each file's path, size, and mtime. It replaces the planned 7-day reprocessing window: a partition that arrives late is loaded whenever it appears, and keys are upserted. A re-run processes 0 files.
- **Lineage:** `etl_runs` (run id, source-manifest SHA-256, per-table stats) and `etl_schema_drift`.
- **Values:** real values are Spanish (`Tarjeta Crédito`, `Negativo`) even where the dictionary lists English, and the contracts follow the data. `product_number` is the card number, treated as PII.
- **PII consequence:** only ~10% of card numbers pass Luhn, so the guard tokenizes any 15–16 digit number, Luhn-valid or not.
- **Demand evidence** is in [DEMAND_ANALYSIS.md](DEMAND_ANALYSIS.md). Cards are ~35% of products, transactions, and product-linked complaints. Call-center contacts can't be attributed to cards: 99.35% of `mentioned_products` IDs don't resolve.

## Deviations from PROJECT_PROMPT.md
- **Data source:** colombia-only invented data → the supplied LATAM Bank dataset (MX/CO/AR), because the challenge requires grounding in the supplied data.
- **Languages:** Spanish only → Spanish + Portuguese, as the challenge requires.
- **Intents:** 5 intents including `transfer` → card-support intents only. The challenge asks for one workflow and forbids money movement.
- **Models and sampling:** a single unspecified model → mixed Haiku 4.5 (triage) and Sonnet 5 (others). The spec's temperatures are kept where the model accepts them (Haiku); otherwise `effort` is used.
- **Terminal nodes:** a single `reject` → separate `reject` and `escalate` nodes, because the challenge scores abstention and escalation separately.
- **Additions:** a data pipeline (ETL), a learned-classifier comparison, a whole-system baseline, customer sessions, and deployment, all required by the challenge.
- **Executor output (step 3):** structured JSON with free-form `args` became **native tool calling**. The structured-output API rejects free-form objects, and native calls give a real ReAct trace. The model can only *propose* the write, through the `propose_block_card` tool. An `escalate_to_human` tool lets it hand off disputes with verified facts.
- **Vector store (step 3):** Chroma → exact in-memory search. The corpus has 35 sections, so a matrix product is simpler, faster, and has no extra dependency.
- **Action confirmations:** fixed templates filled with verified facts, instead of LLM-written text.
- **Customer experience (2026-09-26):**
  - **Brand and voice:** the fictional brand (Lía, Banco Cordillera) and a voice guide, versioned as prompts v1.1.
  - **Pending message:** the customer gets an interim "requested, pending supervisor" message while a block awaits approval.
  - **Templates and quick replies:** warm templates with next steps and policy facts that match the corpus, plus quick-reply suggestions.
  - **Personalization:** personalized welcome **at the channel layer only**. The earlier idea of greeting inside the graph behind a placeholder was dropped, so the customer's name never enters the graph or the LLM.
- **API (step 4):**
  - **PII vault:** in-memory → **SQLite, encrypted** (Fernet plus an HMAC lookup), so pending approvals survive a restart.
  - **Streaming:** progress events plus the *reviewed* final text. Unreviewed LLM tokens are never streamed.
  - **Customer notification:** an in-process pub/sub (`EventBus`). Documented limitation: multiple replicas would need a shared broker such as Redis.
  - **Another customer's thread:** 404 rather than 403, for anti-enumeration consistency with the tools.
  - **Target check:** a deterministic check on the card before a block goes to approval.
- **Frontend (step 5):**
  - **Static export** instead of a Next.js server, so hosting is free and the only paid runtime is the backend. All calls go from the browser to FastAPI (CORS via `FRONTEND_ORIGIN`).
  - **Next 16** instead of the spec's "14+"; it is the current release line, and the app only uses client components.
  - **Approver id:** the auditor panel never sends `approver_id`; the API takes it from the auditor's token (stricter than the spec's body field).
  - **Session tokens in memory only**, and no conversation kept in browser storage (it may hold the customer's raw PII).
- **Evaluation (step 6), budget-driven:**
  - The golden set keeps ~120 cases and all categories, but only a **33-case stratified subset runs live**, once (≈US$2.6, hard cap), instead of 3 runs of everything. Everything that needs no model (fixtures, PII guard, injection regex, intent fallback, language heuristic) runs offline on all cases for free.
  - **No all-Haiku ablation and no LLM judge** for now (cost, and the judge needs human ratings to be valid). Scoring is deterministic.
  - Added an **escalation** category (human requests and disputes) so the "human required" path is measured directly.
- **Data-quality finding (step 4):** 10/2000 demo customers have a duplicated first name ("Lorena Lorena"). The welcome message collapses exact repeats.
