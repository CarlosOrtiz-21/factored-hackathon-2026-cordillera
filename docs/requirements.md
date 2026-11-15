# Requirements

Everything the hackathon evaluates, restated as stable IDs so that code, tests, and docs can cite them. Sources: the public challenge page (five judged dimensions, quality over quantity, deliverables), the problem statement and problem prompt summarized in [CLAUDE.md](../CLAUDE.md) ("Hackathon rules to keep in mind"), and [PROJECT_PROMPT.md](PROJECT_PROMPT.md). Where the brief gave no wording, the ID states our reading of it.

`tests/test_traceability.py` enforces two things: every ID below is cited by at least one test module or one doc, and every citation anywhere in `tests/`, `docs/`, `src/`, and `eval/` resolves to an ID below. A test cites requirements in its module docstring: `Requirements: CTL-01, CTL-02`.

## Judged dimensions (no weights published)

| ID | Dimension | What the page says |
|---|---|---|
| DIM-01 | Technical judgment | Architecture, trade-offs, reliability, safety, production readiness |
| DIM-02 | AI engineering | Backend, frontend, system integration, deployment |
| DIM-03 | Data engineering | Data quality, pipelines, preparation, reproducibility |
| DIM-04 | Machine learning | Modeling approach, evaluation, baselines, performance |
| DIM-05 | Data analytics | Metrics, insights, visualization, decision support |

## Scope (SCP)

| ID | Requirement |
|---|---|
| SCP-01 | One focused workflow (we chose card support), with the reasons recorded |
| SCP-02 | Spanish and Portuguese customer interactions |
| SCP-03 | A normal path resolved automatically |
| SCP-04 | An ambiguous or unsupported request that the system clarifies or abstains on |
| SCP-05 | A path that needs a human, with a structured handoff |
| SCP-06 | Everything synthetic is labeled as such: the bank, the policies, the customers |

## Problem grounded in the data (PRB)

| ID | Requirement |
|---|---|
| PRB-01 | The workflow choice is backed by measurements on the supplied dataset |
| PRB-02 | Data-quality findings are measured and recorded, and the pipeline handles them |
| PRB-03 | Known label defects (no intent labels, `fraud_score` derived from `is_fraud`) are stated and respected |

## A functioning AI system (AI)

| ID | Requirement |
|---|---|
| AI-01 | A deployed, working link |
| AI-02 | The system understands the request, decides, acts, verifies, and escalates |
| AI-03 | Replies are grounded in the customer's records or cited policy; nothing invented |
| AI-04 | Policy answers cite their sources; an unsupported question gets an abstention |
| AI-05 | Customer-facing outcomes (blocked, cancelled, escalated) are fixed text chosen by code |
| AI-06 | A consistent brand voice, with formatting and emoji rules enforced for sensitive topics |

## Controlled automation (CTL)

| ID | Requirement |
|---|---|
| CTL-01 | No side-effecting tool runs before a human decides |
| CTL-02 | The decision comes from a control the customer clicks, never from typed text or the model |
| CTL-03 | A risky write also waits for a supervisor who is not the requester |
| CTL-04 | A write is executed by deterministic code and verified by re-reading before it is reported |
| CTL-05 | Writes are idempotent; a retry never acts twice |
| CTL-06 | Every figure in a reply must come from the evidence (reply check in code) |
| CTL-07 | Every node fails closed: invalid model output, failed verification, exhausted revisions |
| CTL-08 | The handoff is a structured case: verified facts tied to their tool calls, the customer's statements apart, a priority set by code |

## Data and execution boundaries (SEC)

| ID | Requirement |
|---|---|
| SEC-01 | The model never sees raw personal data; inputs and tool outputs are tokenized |
| SEC-02 | Tools enforce ownership; another customer's resource reads as not found |
| SEC-03 | Sessions expire and are checked at the channel and on every tool call |
| SEC-04 | Direct and indirect prompt injection are resisted, with a deterministic backup layer |
| SEC-05 | Logs, traces, and the audit trail hold tokens only |
| SEC-06 | Secrets never live in the repository or in commands |
| SEC-07 | A public deployment is capped in spend and rate |

## Sound data and ML practice (DML)

| ID | Requirement |
|---|---|
| DML-01 | An incremental, idempotent pipeline with lineage and quarantine |
| DML-02 | Data contracts checked against the data, not the dictionary |
| DML-03 | A learned component compared against a baseline on a leakage-safe split |
| DML-04 | A learned component that fails its gate is not deployed, and the result is published |
| DML-05 | Team-generated labels are versioned and validated by a blind human review |

## Measured quality and failure handling (EVL)

| ID | Requirement |
|---|---|
| EVL-01 | Baseline vs. system on the same held-out cases |
| EVL-02 | Cases include bad or missing data, expired sessions, unauthorized access, direct and indirect injection, tool failures, multilingual ambiguity |
| EVL-03 | Metrics: safe automated resolution, containment, escalation quality, unsafe outcomes as n/N, p50/p95 latency, cost per case and per resolution |
| EVL-04 | Results broken down by language and segment, with model and prompt versions recorded |
| EVL-05 | Offline results, simulations, and projections are labeled as such |
| EVL-06 | Scoring is deterministic; the golden set is held out from training and from rule tuning |
| EVL-07 | Every failure is published and triaged |

## A credible route to operation (OPS)

| ID | Requirement |
|---|---|
| OPS-01 | An append-only audit trail of sources, rules, and execution records, never chain-of-thought |
| OPS-02 | State survives a restart (checkpoints, vault, pending decisions) |
| OPS-03 | Reproducible setup: pinned versions, scripted deploy, data fetched at startup from a private store |
| OPS-04 | Limitations and what a bank would replace are written down |

## Design justification (DSN)

| ID | Requirement |
|---|---|
| DSN-01 | Trade-offs across autonomy, accuracy, latency, cost, and oversight are explicit |
| DSN-02 | Model choices are justified and configurable per agent |
| DSN-03 | Routing and gates are code; prompts are never the permission layer |

## Deliverables (DLV)

| ID | Requirement |
|---|---|
| DLV-01 | Public repository named `factored-hackathon-2026-<team>` |
| DLV-02 | A deployed link |
| DLV-03 | Four to six slides on approach, results, and decisions |
| DLV-04 | A video pitch that demonstrates the working system |
