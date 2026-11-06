# Lía · AI-first card support with a human in the loop

Entry for the **Factored AI & Data Hackathon 2026**. Lía is the virtual assistant of
**Banco Cordillera**, a **fictional** bank. She handles one workflow end to end, **card
support**, in **Spanish and Portuguese**: card status, recent transactions, card policy
questions answered from documents, blocking a card (with human approval), and handing over
to a human advisor when a case needs one.

> All customers and transactions come from the hackathon's **synthetic** LATAM Bank dataset,
> and the policy corpus is **synthetic** too. No real money moves: there is no transfer tool.

**Live demo:** _link added after deployment (step 7)_ · **Evaluation:** [docs/EVAL_REPORT.md](docs/EVAL_REPORT.md)

## What makes it safe

| Rule | How it is enforced |
|---|---|
| No side effect before a human approves | The agents only have read tools. A block is a *proposal*; a deterministic node (no LLM) runs it after an auditor approves, then re-reads the card to verify it. |
| The approval gate is code, not a prompt | `requires_human_approval = is_write(action) or risk_score > 0.7` |
| Segregation of duties | The approver must have the auditor role and cannot be the requester; approvals expire after 10 minutes. |
| The LLM never sees personal data | Presidio + custom recognizers turn card numbers, IDs, and emails into tokens (`<TARJETA_1>`) before any model call; replies show `•••• 1234`. |
| Customers only see their own data | Ownership is checked inside every bank tool; another customer's card answers "not found". |
| Every node fails closed | Invalid critic output blocks the reply; an unverified action escalates; a failed triage falls back to a trained classifier or escalates. |

## Results (live, 33 held-out cases, real API calls)

| | System | Baseline (one LLM with all tools) |
|---|---|---|
| Correct | **97%** (32/33) | 79% (26/33) |
| Unsafe outcomes | **0/33** | 3/33 (blocked cards with no human approval) |
| Writes approved by a human | **100%** | 0% |
| Missed escalations | **0/9** | 3/9 |
| Cost per case | US$0.017 | US$0.011 |

A single run on a small stratified subset, chosen to keep API spend minimal. Method,
breakdowns by language, segment, and category, and limitations are in
[docs/EVAL_REPORT.md](docs/EVAL_REPORT.md).

## Architecture

```text
browser (Next.js, static) ──SSE──> FastAPI ──> PII guard ──> LangGraph
   chat + auditor panel           sessions,                   triage (Haiku 4.5)
                                  approvals                     ├─> executor: explicit ReAct, read tools (Sonnet 5)
                                                                ├─> policy RAG: BM25 + e5, cited (Sonnet 5)
                                                                └─> critic (Sonnet 5) ─> human_approval (interrupt)
                                                                      ─> tool_executor ─> verify ─> respond
                                                                reject / escalate (structured handoff)
```

- **Data:** S3 → DuckDB (bronze/silver/gold, contracts, quarantine, lineage) → SQLite mock
  bank. See [docs/DATA_QUALITY.md](docs/DATA_QUALITY.md) and
  [docs/DEMAND_ANALYSIS.md](docs/DEMAND_ANALYSIS.md).
- **Learned components:** an e5 + logistic-regression intent classifier (triage fallback,
  macro-F1 0.84) and a fraud model that **failed its deploy gate** and is not used
  ([docs/models/](docs/models/)).
- **Design record:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Run it locally

Requirements: Python 3.11 with [uv](https://docs.astral.sh/uv/), Node 20+, an Anthropic API key.

```bash
uv sync
cp .env.example .env                       # add ANTHROPIC_API_KEY (and the S3 keys for the ETL)
uv run python -m pipelines.etl.run         # builds data/bank.db from the hackathon dataset
uv run python -m pipelines.models.intents  # trains the intent fallback (~1 min)
uv run uvicorn --factory cardagent.api.main:create_app --port 8000

cd frontend && npm install && cp .env.example .env.local && npm run dev   # http://localhost:3000
```

Tests (no API calls): `uv run pytest`. Free offline evaluation: `uv run python -m eval.offline`.

The demo login codes are set with `DEMO_LOGIN_CODE` and `AUDITOR_LOGIN_CODE` (the defaults
in `config.py` are for local use only).

## Repository map

| Path | Contents |
|---|---|
| `src/cardagent/` | schemas, PII guard + encrypted vault, mock bank + tool registry, agents, graph, API |
| `pipelines/` | ETL, demand analysis, learned components |
| `eval/` | golden set (120 cases, 31% PT), offline + live evaluation, report generator |
| `frontend/` | Next.js static site: customer chat and auditor panel |
| `corpus/policies/` | synthetic card policies (ES, PT) |
| `datasets/` | team-generated intent labels and the injection dev set |
