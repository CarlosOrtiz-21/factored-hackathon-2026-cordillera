# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Entry for the **Factored AI & Data Hackathon 2026** (submission deadline **2026-10-05**; the developer works solo and part-time). The Python package is `src/cardagent/`, managed with **uv** on Python 3.11 (pinned `<3.12`). The Spanish spaCy model is a pinned dependency in `pyproject.toml`. Copy `.env.example` to `.env`; the user fills in the AWS and Anthropic keys, and you never put keys in commands or files. Steps done: **1** (schemas, config, PII guard + vault), **1b** (ETL + demand analysis), **1c** (learned components), **2** (mock bank API: sessions, tools, registry), **3** (agents, graph, HITL), **4** (FastAPI API with SSE, auth, and approvals; validated live with approve, reject, and expire), and **5** (static Next.js frontend: customer chat + auditor panel; validated by the user in the browser). **6** (golden set, offline eval, live run of 33 cases for US$0.89: system 32/33 correct and 0 unsafe vs. baseline 26/33 and 3 unsafe; see `docs/EVAL_REPORT.md`). The live eval's one system miss (a transient tool outage made the model escalate) is fixed in code: `read()` in `graph/build.py` retries a read once on `tool_unavailable`; re-measuring the 8 tool-failure cases live is pending the user's OK (~US$0.15). Next: **7**, deploy. **Deployment must be free or near-free** (keep that in mind in every choice).

```bash
uv sync                                         # install everything
uv run pytest                                   # all tests (unit + ETL on a synthetic fixture)
uv run pytest tests/test_pii.py -k luhn         # single test
uv run ruff check src pipelines tests && uv run ruff format src pipelines tests
uv run python -m pipelines.etl.extract --list   # inspect the S3 bucket (read-only)
uv run python -m pipelines.etl.run              # extract + transform + bank.db + DATA_QUALITY.md
uv run python -m pipelines.etl.run --skip-extract   # reuse data/raw (~4 min first run, ~10 s after)
uv run python -m pipelines.etl.run --rebuild-bank  # recreate bank.db: WIPES sessions, card_actions, and blocked cards
uv run python -m pipelines.analysis.demand      # docs/DEMAND_ANALYSIS.md
uv run python -m pipelines.models.intents       # intent classifier -> models/intent_v1.joblib + docs/models/INTENT_MODEL.md (~1 min)
uv run python -m pipelines.models.intents --review   # agreement/kappa once review_sample.csv is filled
uv run python -m pipelines.models.fraud         # fraud model + deploy gate -> docs/models/FRAUD_MODEL.md (~2 min)
uv run python scripts/chat_cli.py                # live terminal chat with Claude + HITL approval prompt
uv run python scripts/compare_voice.py            # prompts v1.0 vs v1.1 side by side -> docs/voice_comparison.md
uv run uvicorn --factory cardagent.api.main:create_app --port 8000   # API (add APPROVAL_TTL_MINUTES=1 for a quick expiry demo)
bash scripts/api_demo.sh [approve|reject|expire]    # curl walkthrough of the three HITL flows against a running server
uv run python -m eval.offline                   # FREE: offline eval on all 120 golden cases -> eval/results/offline.json
uv run python -m eval.run                       # dry run: plan + cost estimate, calls nothing
uv run python -m eval.run --yes --max-usd 3     # PAID: live subset (33 cases), system + baseline, hard USD cap
uv run python -m eval.report                    # docs/EVAL_REPORT.md from offline.json + latest live-* run
cd frontend && npm install && cp .env.example .env.local   # once
cd frontend && npm run dev                      # http://localhost:3000 (chat) and /auditor/ (panel)
cd frontend && npm run typecheck && npm run build   # static site in frontend/out/
```

## Frontend (`frontend/`)

- **Static export** (`output: "export"`): `next build` writes plain files to `out/`, so it is free to host (Vercel Hobby, Cloudflare Pages, GitHub Pages). There are no Next server features (no API routes, no middleware, no rewrites); everything calls FastAPI from the browser at `NEXT_PUBLIC_API_URL`. The backend's `FRONTEND_ORIGIN` must match the site's origin (CORS).
- Pinned: Next 16.4.2, React 19.3.1, Tailwind 4.3.3 (tokens in `src/app/globals.css` `@theme`), TypeScript 5.9.3, lucide-react, `@microsoft/fetch-event-source` 2.0.1.
- **SSE client** (`src/lib/sse.ts`): fetch-event-source retries by default. Every error is made fatal and `openWhenHidden: true` is set, because a retried `POST /api/chat/stream` would run the same turn twice.
- **Tokens live in memory only** (a reload means logging in again). The conversation is never stored in browser storage, because the customer's own messages may contain raw PII.
- Flow: the chat stream ends at `approval_required` → the UI opens `GET /api/chat/{thread}/events` and waits. The auditor panel polls `/api/approvals/pending` every 5 s, decides with a two-step confirm, and shows `/api/threads/{id}/state` (audit trail, usage).
- The user's terminal is **Windows PowerShell 5.1**: `&&` doesn't exist there, so give them `cd frontend; npm run dev` (or two lines).
- Always write `useEffect` bodies with braces. Newer browsers make `scrollIntoView()` return a Promise, and an expression-bodied effect returned it, which React rejects (found live in step 5).
- Accessibility (WCAG 2.1 AA review done): input borders use `--color-line` (3.7:1), badge text uses `sunrise-700`, and the chat log is `aria-busy` while streaming so screen readers read the finished answer instead of every chunk. Countdowns stay outside live regions.

- **Learned components:**
  - The intent classifier, e5 + LR (`models/intent_v1.joblib`, which stores the model, threshold, and labels), is the **triage fallback**. It acts only if confidence ≥ the stored threshold, else the case escalates.
  - The fraud model failed its deploy gate (`deploy: False` in the artifact). **Don't wire a fraud flag into the tools.**
  - The intent labels are team-generated (`datasets/intents/`); the file is versioned, and any change means bumping `v1`.
- On Windows, the HuggingFace symlink warning when loading e5 is harmless.
- **Bank service** (`src/cardagent/bank/`):
  - Tools only run through `ToolRegistry.call()`/`invoke()`. That path enforces the allowlist, Pydantic arguments, session expiry, and an `ApprovalGrant` for writes.
  - Give agents `registry.read_only()`; `block_card` is only called by `tool_executor`.
  - Card ownership is enforced inside the tools. Another customer's card, or a token from another thread, returns `not_found`, which prevents enumeration.
  - `ApprovalGrant` is bound to one idempotency key. The approver must have the auditor role and must not be the requester, and the grant expires.
  - `invoke()` returns `ToolResult` with stable `error_code`s for agents. `fault_injector` simulates outages for the evaluation.
  - Sessions store only the SHA-256 of the token.
  - Transaction look-back windows are relative to `bank_as_of_date` (2026-06-17), because the dataset is a static snapshot.

- If imports fail after adding a new top-level module under `src/`, run `uv sync --reinstall-package cardagent`.
- `pipelines/` is not packaged; run it as a module from the repo root.
- Data artifacts:
  - `data/raw/`: the S3 mirror plus `_manifest.json`.
  - `data/warehouse.duckdb`: bronze/silver/gold tables, `quarantine_*`, and `etl_*` lineage tables.
  - `data/bank.db`: SQLite for the mock API.

Key documents:
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): approved Phase 0 design. It is the **source of truth** for the state schema, the graph, decisions, the eval plan, and the roadmap.
- [docs/PROJECT_PROMPT.md](docs/PROJECT_PROMPT.md): the original spec, in Spanish. ARCHITECTURE.md lists where the design deviates from it.

## Working mode (user preference)

- The user is learning while building. **Explain each concept before implementing it**, go one step at a time, and **wait for confirmation** at the end of each roadmap step.
- Close each step with three things: what was done and why, the key concepts involved, and how the user can test it themselves.
- Fix errors on the spot. Never skip an error or leave a silent TODO.
- Priorities, in order: **operation safety > traceability > clear, teaching-oriented code > features.**
- Don't replace the explicit ReAct loop, or any other hand-built learning component, with a prebuilt agent without asking first.
- Talk with the user in Spanish. Code, comments, and repo docs are in English.

## API (`src/cardagent/api/`)

- **App factory:** `create_app(container)`; `build_container(settings, llm=..., clock=...)` wires everything. Tests inject `FakeLLM`, a fake retriever, and a controllable clock (see `tests/test_api.py`).
- **Auth (mock identity provider):**
  - Customers log in with `customer_id` plus a demo code (`DEMO_LOGIN_CODE`, default 246810). Every failure returns the same 401.
  - Auditors log in separately (`AUDITOR_LOGIN_CODE`, default 135790), and their tokens live in `staff_sessions`. A customer token on an auditor endpoint gets 403.
  - The approver id comes from the auditor's token, never from the request body.
- **Threads:** the `threads` table records which customer owns each conversation. Another customer's thread returns **404**, like a thread that doesn't exist.
- **SSE events** (`api/runner.py`): `thread`, `node_start` (from `stream_mode=["tasks","updates"]`), `interim`, `approval_required`, `token`, `final`, `error`.
  - We **never stream raw LLM tokens**, because that would show text before the critic reviewed it. `token` events are the reviewed final text, sent in pieces.
- **Waiting for approval:** the customer's first stream ends at `approval_required`, and they then wait on `GET /api/chat/{thread_id}/events`. The in-process `EventBus` delivers the auditor's decision live; if the decision already happened, the endpoint replays the final result.
- **Expiry:** a background sweep (every 30 s, and also on each `GET /api/approvals/pending`) resumes expired approvals, so the customer is told the request lapsed.
- **Persistence:** `data/checkpoints.db` (LangGraph `SqliteSaver`), `data/vault.db` (the **encrypted** PII vault: Fernet ciphertext plus an HMAC lookup; key from `VAULT_KEY` or `data/vault.key`), and runtime tables in `bank.db` (`sessions`, `staff_sessions`, `threads`, `approval_requests`, `card_actions`). A pending approval survives a server restart.
- **Target check:** before a block goes to approval, the executor node runs `get_card_status` in code. A card that doesn't exist, isn't the customer's, or is already Blocked or Closed goes back to the model as `cannot_propose: ...`. The result travels in `proposed_action.target_check` as critic evidence. It does NOT count as "the customer mentioned this card"; that control still uses `pii_token_refs` and prior reads.
- **Critic input for proposals:** it says the customer won't see the draft and asks the critic to judge the action. Found live: the critic kept revising a draft nobody reads, which exhausted retries and escalated a valid block.
- **Windows curl:** send JSON bodies with non-ASCII text via `--data-binary @-` from Python's `json.dumps`; command-line arguments get mangled by the console code page.

## Evaluation (`eval/`, step 6)

- **Budget:** the user wants minimal API spend. Never launch `eval.run --yes` without their explicit go-ahead; the LLM judge is deferred (it needs ~30 human ratings).
- **Golden set** `eval/golden/golden_v1.jsonl` (team-generated, 120 cases, 31% PT, 33 marked `live`). Cases name a customer *profile* (`multi`, `one_credit`, `with_blocked`, `with_tx`); `FixtureFactory` picks a real customer from bank.db by hash of the case id and fills placeholders (`{credit_last4}`, `{other_number}`, `{merchant}`, `{canary}`...). Any change to cases means bumping to `golden_v2`.
- **Isolation:** each case runs on a throwaway copy of bank.db (`eval/env.py`), with its own vault, faults (`always:`/`first:` tool), and poison (policy chunk or merchant name). The real `data/bank.db` is only copied.
- **System runner** mirrors the API (expired session → `session_rejected` before the graph; PII guard; graph; the eval plays the auditor `auditor-eval` with the case's `hitl` decision, default reject). **Baseline:** one LLM (executor model) with all tools, `block_card` executed immediately with a code-filled grant (`no-human`), policy search, escalation; its answer/abstain/refuse outcome is self-reported via `final_answer`.
- **Scoring is deterministic** (`eval/checks.py`): outcome (`resolve`/`clarify` both map to `answer`), facts by substring, unsafe flags (write without a human, write against the human's decision, wrong card, raw PII, canary, prompt leak, other customer's data, false block claim), and quality flags. Metric definitions live in `eval/metrics.py`.
- **Don't tune rules on the golden set.** The injection regex (one alternative per attack family, `re.VERBOSE`) is tuned on `datasets/injection_dev_v1.csv` and must flag 0 of the 505 intent training utterances (`tests/test_injection_regex.py`). It went from 2/10 to 10/10 on golden v1, but that figure is optimistic because the golden misses were seen first; the report says so.
- **Manual review notes** for a live run go in `eval/results/<run>/notes.md`; `eval.report` includes them, so regenerating never loses them.

## Brand and voice

- **The assistant is Lía, of Banco Cordillera.** The bank is FICTIONAL, invented for the hackathon; see `src/cardagent/brand.py` and the corpus README. Its phone line `#2222` and advisor hours are fictional too.
- **Tone:** warm, with minimal emojis. Use tú in Spanish (vos if the customer uses it) and você in Portuguese. At most one emoji, and only in greetings or positive confirmations. **Never** use emojis around loss, theft, fraud, cancellations, or escalations. The rules live in `agents/style.py` and are injected into the executor, RAG, and critic prompts.
- **Prompt versions:** `agents/prompts.py` holds `PROMPTS["v1.0"]` (functional, frozen for comparison) and `PROMPTS["v1.1"]` (Lía's voice), and `v1.2` (v1.1 plus a card-reference rule; current). The `PROMPT_VERSION` setting selects one, and the audit trail records it. Bump the version whenever a prompt changes.
- **Critic:** it returns `approved_response` only when it fixed something; otherwise the draft goes out unchanged, which preserves the voice. Style alone never triggers a revision.
- **Customer-facing text decided by code** lives in `agents/templates.py`: confirmations, pending approval, cancellation, escalation (with a `CRD-XXXXXX` case id), refusals, welcome, suggestions (`SUGGESTIONS`), and progress lines (`PROGRESS`). Policy facts quoted in templates must match the corpus; `test_policy_facts_in_templates_match_the_corpus` enforces it, and the sections are listed in `POLICY_SOURCES`.
- **Personalization:** the customer's first name is used ONLY by the channel layer (`templates.welcome`, called by the CLI now and the API later). It never enters the graph, an LLM call, or the audit trail; a test enforces it.
- **UX fields in the state:** `interim_response` is shown while a block awaits approval, and `suggestions` holds quick replies.
- **Card choices:** when Lía ends with a question and `list_cards` ran in that turn, `suggestions` become the customer's masked cards (e.g. `Crédito •••• 1382`). They are filtered by the card kind the customer mentioned and, for blocks, to active cards only. See `card_choices()` in `graph/build.py`.
- **Formatting fixes belong in code:** `tidy_card_mentions()` collapses "1382 (•••• 1382)". Prompt v1.2 added a rule for it, but the model kept duplicating digits in the live run. Lesson: when a live run shows a formatting defect, fix it deterministically instead of adding more prompt text.

## What we are building

An AI-first **card-support** assistant: card status, card transactions, card block (a write that requires HITL approval), card-policy questions answered through RAG, human handoff, and refusal of out-of-scope requests.
- There is **no money movement**, so there is no `transfer` tool.
- Unrecognized charges are verified, then **escalated** with a structured handoff. The system never opens a dispute on its own.
- The system must handle **Spanish and Portuguese**.

## Golden rule

**No side-effecting tool (`block_card`) runs before the `human_approval` node.**
- The executor has read tools only and emits writes as a `proposed_action`.
- The deterministic `tool_executor` node (no LLM) runs the write after approval, then a `verify` step re-reads the state before anything is reported to the customer.
- The HITL gate is code:

```python
requires_human_approval = is_write(proposed_action) or risk_score > 0.7
```

**Ownership rule:** the tools check that the session owns each customer, card, and account. The API sets `customer_id` from the session; an LLM never infers it. Prompts are never the permission layer.

## Graph (LangGraph), as implemented (`src/cardagent/graph/build.py`)

- **Routing:** the `route_*` functions are pure Python. The HITL rule is in the critic node, as code.
- **Executor tools:** the executor gets the read-only registry plus two *intent* tools. `propose_block_card` executes nothing and records a `proposed_action` whose idempotency key is a hash computed by code. `escalate_to_human` hands the case over. `block_card` is only called by `tool_executor` with an `ApprovalGrant`.
- **Resuming after an interrupt:** `human_approval` re-runs from the top on resume, so keep it free of side effects before `interrupt()`. Resume with `graph.invoke(Command(resume={decision, approver_id, approver_role}), config)`.
- **Confirmations are code:** "blocked", "cancelled", "expired", and the reject/escalate messages are templates in `agents/templates.py`. The LLM never phrases an action outcome. Outgoing text goes through `mask_tokens`, which shows cards as `•••• 1234`.
- **Read retries are code:** `read()` retries a READ tool once on `tool_unavailable` (executor tools, the target check, and `verify`), and the audit event records `retried`. Writes are never retried automatically; a failed write goes to verify → escalate. Two reads failing even after their retry → `tool_failure` → escalate.
- **Deterministic guards** in `agents/guards.py`: an injection regex (it can force a rejection even if the LLM misses it), a raw-PII check on drafts, and "action target must appear in the evidence".
- **State:** `turn_input()` builds each turn's input. Per-turn fields reset; `messages`, `audit_trail`, `usage`, and `react_steps` accumulate. State values are JSON-serializable because the checkpointer persists them.
- **RAG:** `rag/retriever.py` does exact in-memory hybrid search: BM25 plus e5, fused with RRF. Chroma was dropped because the corpus has 35 chunks. The corpus is in `corpus/policies/{es,pt}` and is **synthetic**. The citation key is `lang/doc#section`, and citations outside the retrieved set force a revision.

## Graph (design reference)

- Node sequence: `triage` → `executor` (explicit ReAct, read tools only, at most 5 iterations) or `policy_rag` → `critic` (at most 2 revise loops) → `human_approval` (`interrupt()`) → `tool_executor` → `verify` → `respond`.
- Two separate exits:
  - `reject`: safe refusal for injection or out-of-scope requests.
  - `escalate`: builds the structured `HandoffPayload` (request, verified facts, actions taken, evidence, open questions).
- Fallback rules (the full diagram is in ARCHITECTURE.md §4):
  - Every node fails **closed**: invalid critic output → `block`; failure to verify → `escalate`.
  - If triage's LLM call fails twice, the learned TF-IDF classifier takes over.
  - An expired session never enters the graph.
- `audit_trail` is append-only and holds tokens only.
- `pii_token_refs` holds references only; real values stay in the vault.

## LLM usage (Anthropic)

- **Models (mixed):** triage uses `claude-haiku-4-5`; executor, policy_rag, and critic use `claude-sonnet-5`. They are configurable per agent via `TRIAGE_MODEL`, `EXECUTOR_MODEL`, `RAG_MODEL`, and `CRITIC_MODEL`. The official `anthropic` SDK is called directly in LangGraph nodes; no LangChain model wrappers.
- **Every call gets its parameters from `cardagent.llm.profiles`:** `get_agent_configs()[agent]` → `request_params(config)`. Never hand-write `temperature`, `effort`, or `max_tokens`.
  - Haiku 4.5 accepts `temperature` but rejects `effort`.
  - Sonnet 5 and Opus 5 reject `temperature` and `top_p` (400) and use `output_config.effort`. Their adaptive thinking is on by default and shares the `max_tokens` budget.
  - Each agent in `AGENT_SPECS` declares both its temperature (from the original prompt) and an effort level; the model profile picks the one to send. An invalid config raises `LLMConfigError` at startup.
  - To add a model, add its row to `MODEL_PROFILES`, including prices; `estimate_cost()` uses them for the cost metrics.
- **anthropic SDK 1.x:** `temperature` is no longer a typed argument; `request_params()` sends it through `extra_body`, which is only valid for models that accept it (Haiku). `effort` and the structured-output `format` both live in `output_config` and are merged in `LLMClient._call`. Schemas go through `anthropic.transform_schema`, which moves unsupported `min`/`max`/`pattern` constraints into descriptions; Pydantic re-validates on the client.
- **Workspace:** if the API key isn't scoped to a workspace, set `ANTHROPIC_WORKSPACE_ID` in `.env`; it is sent as the `anthropic-workspace-id` header.
- **Schema strictness:** be strict on safety-relevant fields (enums, flags, tokens, scores) and lenient on cosmetic ones (normalize or truncate them with a `field_validator`). Constraints like `max_length` only reach the model as a description after `transform_schema`, and the model may ignore them. In the first live run, a 300-character `rationale` limit discarded valid triages and wrongly escalated the case.
- **`llm/client.py`:** `AnthropicLLM` never raises into the graph. API errors, refusals, truncation, and invalid JSON all become `error`, with `value=None`. Tests use `FakeLLM`, whose responses are scripted per agent and whose calls are recorded in `.calls`.
- **Ablation:** the eval runs the golden set with the mixed config and again with all four set to `claude-haiku-4-5`. Only env vars change.
- **Structured-output chain:** `client.messages.parse()` with Pydantic → `json_repair.repair_json()` → Pydantic validation → safe response plus a logged error. Never continue with unvalidated data.
- **Refusals:** check `stop_reason == "refusal"`. A refusal counts as a node failure and takes the safe path.
- **Untrusted input:** retrieved policy chunks and tool outputs are delimited and treated as data, never as instructions.
- **Unit tests** use a fake LLM client. Only the eval makes real API calls.

## PII

- Presidio runs with a Spanish spaCy model and custom recognizers: CC, NIT, CURP, DNI, cards (Luhn-validated), accounts, mobile numbers, and email. See `src/cardagent/pii/`. Presidio gotchas already handled there:
  - Every `PatternRecognizer` needs a **unique `name`**, or the context enhancer applies another recognizer's context words.
  - Context words must not be spaCy stopwords (`build_analyzer` un-stops them).
  - spaCy PER hits are post-filtered, because it tags sentence-initial words as names.
  - Replacement is done by `PIIGuard` itself (no `presidio-anonymizer`), so that tokens come from the vault.
- Pseudonymization is reversible: values become tokens such as `<TARJETA_1>`, stored in a vault keyed by `thread_id` (in memory, behind an interface).
- **The LLM never sees raw PII**, neither from user input nor from tool outputs, which are tokenized and show only the last 4 digits.
- Only tools and `tool_executor` can resolve tokens.
- Logs, traces, and the audit log contain tokens only.

## Stack

- **Backend:** Python 3.11, uv, FastAPI, Pydantic v2 + pydantic-settings, LangGraph + `langgraph-checkpoint-sqlite`, `anthropic`, `presidio-analyzer` + Spanish spaCy model (`es_core_news_md`), `json-repair`.
- **Data and retrieval:** DuckDB (ETL) → SQLite `bank.db` (mock bank); Chroma + `intfloat/multilingual-e5-small` + BM25 for hybrid retrieval.
- **ML and tracing:** scikit-learn (intent classifier), LangSmith (traces).
- **Frontend:** minimal Next.js (App Router), Tailwind, lucide-react, `@microsoft/fetch-event-source`.
- Pin all versions. **Check the installed LangGraph version's API** (interrupts, checkpointers, streaming) before writing code against it.

## API contract

- `POST /api/chat/stream`: SSE with typed events `node_start`, `token`, `tool_call`, `approval_required`, `final`, `error`.
- `POST /api/approve`: body `{thread_id, decision, approver_id, comment}`; resumes the thread with `Command(resume=...)`. The auditor role is required, **the approver cannot be the requester**, and approvals expire after 10 minutes (the operation is cancelled).
- `GET /api/threads/{thread_id}/state`: current state plus `audit_trail`, for the auditor panel.

## Roadmap (each step ends with its own tests, then waits for the user's confirmation)

| Date | Step |
|---|---|
| Sep 25–26 | 1 schemas, config, Presidio + vault |
| Sep 27–28 | 1b ETL (contracts, quality report, lineage) + demand analysis + intent classifier |
| Sep 29 | 2 mock bank API + sessions |
| Sep 30–Oct 1 | 3 agents + graph + HITL |
| Oct 2 | 4 FastAPI SSE + approve + state |
| Oct 3 | 5 minimal frontend ✅ (static export) |
| Oct 4 | 6 golden set (~120 cases, ~30% PT) + baseline vs. system eval → `docs/EVAL_REPORT.md` |
| Oct 5 | 7 deploy (backend on Render or Railway, frontend on Vercel) + slides + video |

**Cut order if behind schedule:** frontend polish → PT policy translations → LangSmith → eval variance runs.

## Hackathon rules to keep in mind

- **Three paths to demonstrate:** normal automated resolution; an ambiguous or unsupported request (clarify or abstain); a human-required escalation.
- **Audit artifacts** are sources, policy rules, and execution records, never chain-of-thought.
- **Evaluation** compares baseline vs. system on the same held-out cases:
  - report safe automated resolution, containment, escalation quality (missed and unnecessary transfers), unsafe outcomes as n/N, p50/p95 latency, and cost per case and per resolution ("not defined" if zero);
  - break results down by language and segment, and record model and prompt versions;
  - label offline results, simulations, and projected savings as such.
- **Test cases must include** bad or missing data, expired sessions, unauthorized access, direct and indirect injection, tool failures, and multilingual ambiguity.
- **The policy corpus is synthetic** and must be labeled as such.
- **Submission:** public repo `factored-hackathon-2026-<team-name>`, a deployed link, 4–6 slides, and a video.

## Dataset: LATAM Bank v1.0.0 (synthetic)

~19M rows in 13 tables covering MX/CO/AR from 2023-06-17 to 2026-06-17, all text in Spanish. The full schema is in `docs/LATAM_Bank_Complete_Data_Dictionary (2).pdf`. **Measured facts are in `docs/DATA_QUALITY.md` and `docs/DEMAND_ANALYSIS.md`; trust them over the dictionary.**

- **Access:** read-only S3 bucket `factored-datathon-2026-s3-157725502942-us-east-2-an`, prefix `data/`, pulled by `pipelines/etl/extract.py` with `boto3` using the keys in `.env`. The AWS CLI isn't installed and isn't needed. The PDF, `data/`, and `.env` are gitignored.
- **Layout:** UTF-8 CSV files with a BOM. Dimensions are one snapshot file each; facts are hive-partitioned daily. Integers are written as `250.0` and booleans as `True`/`False`.
- **Values differ from the dictionary:**
  - Many values are Spanish: `product_type` is `Tarjeta Crédito`/`Tarjeta Débito`, sentiment is `Negativo`.
  - Product currencies have no MXN.
  - `transaction_country` mixes `Mexico` and `México` (normalized in silver).
- **Label reality:**
  - `contact_reason` equals `reason_category` (6 values).
  - `detected_intents` is constant, and the transcripts are 42 templates.
  - `mentioned_products` IDs are 99% unresolvable.
  - So there are no intent labels in the data: the intent classifier uses team-generated labels, and the second learned component uses `is_fraud`.
  - `fraud_score` looks derived from the label; never use it as a feature.
- **Card numbers** (`products.product_number`) are 16 digits and ~90% fail Luhn, so the PII guard tokenizes any 15–16 digit number.
- **Quality issues actually observed:**
  - 0 duplicate keys (the dictionary claims ~2%).
  - 14% of transcripts are missing the required `duration_seconds` and are quarantined.
  - ~6% of dimension rows have a `last_updated` date in the future.
  - 25–79% of fact rows have an event date different from `process_date`.
- **Leakage:** split by customer and time, or by paraphrase group for synthetic utterances; never by row. Keep post-outcome fields (`was_resolved`, `resolution`, survey scores) out of features.
