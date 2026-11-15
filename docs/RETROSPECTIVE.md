# Retrospective: Lía against the winners

Written on 2026-11-13, after the hackathon, by reading the winners' public repositories. It compares our entry (Lía, Banco Cordillera) with the first place, **Faro** ([gafnts/factored-hackathon-2026-faro](https://github.com/gafnts/factored-hackathon-2026-faro)), and the second place, **LedgerLens** ([eduardorestrepo89/factored-hackathon-2026-ledgerlens](https://github.com/eduardorestrepo89/factored-hackathon-2026-ledgerlens)). Everything about them comes from their README, docs, and commit history as of that date; their numbers are theirs, reported as they report them. The point is to learn, so the tone is blunt.

## The finding that outweighs the rest

Our steps 1 to 4 were committed by 2026-09-26. Steps 5, 6, and 7 (frontend, evaluation, deployment, slides, video) were done between 2026-11-05 and 2026-11-06, a month after the 2026-10-05 deadline. Whatever was submitted on time, it was not the system described in this repository. Both winners had a deployed link, a report, slides, and a video on 2026-10-05.

Every other lesson below is secondary to this one: **a hackathon entry is what exists at the deadline.** A deployed, ugly, half-evaluated system on day 3 beats a complete one on day 40.

## Context

| | Faro (1st) | LedgerLens (2nd) | Lía |
|---|---|---|---|
| Team | 1 person (gafnts: 1,070 commits) | 2 people (sebasr0 158, Eduardo ~90 in the window) | 1 person, part time |
| Commits in the window | ~595 between Sep 25 and Oct 5, peak 121 on Oct 2 | 248 between Sep 27 and Oct 6, peak 72 on Oct 3 | Steps 1–4 on Sep 25–26; then nothing until Nov 5 |
| Starting point | From scratch, on AWS with Terraform | AWS's FAST template (auth, deploy, streaming already solved) | From scratch |
| Stack | AgentCore Runtime + Gateway, LangGraph, Cognito, Cedar, DynamoDB, dbt + DuckDB, Terraform, Claude Haiku 4.5 | AgentCore + Strands, Cognito, Cedar, Aurora DSQL, CDK, Claude Haiku 4.5 on Bedrock | LangGraph, FastAPI, SQLite, Presidio, Cloud Run, GitHub Pages, Claude Haiku 4.5 + Sonnet 5 |
| Cost posture | Full AWS account | Full AWS account | Free tier, hard daily API cap |

Faro's solo author shipped roughly five times our commit volume in the same ten days and kept going for a month after. That is not a reason for the result, but it sets the scale: there was no shortage of hours available in the window; we didn't spend them.

## What Faro did that we didn't

Faro is the entry most like ours in spirit (card support, writes only on confirmation, structured handoff, permissions in code), which makes the gap instructive: same idea, far more rigor.

### 1. It turned the rubric into requirement IDs and cited them everywhere

`docs/prerequisites.md` restates everything the organizers evaluate as stable IDs (SCP‑01, CTL‑04, EVL‑13, OPS‑11..., in Faro's own numbering). Every ADR, every doc, and every PR cites the IDs it covers. A judge can trace any requirement to the code and the evidence. We read the rules once, summarized them in CLAUDE.md, and never mapped the build back to them.

### 2. It chose the workflow with a pre-registered rule, committed before the numbers

ADR-0003 fixed four gates (field completeness, customer state, writable reference outcomes, a learned label with held-out signal) **before** running them, with a stated favorite and a stated guard against bending the rule toward it. When no candidate passed all four, the revisit was written in the open, with what each alternative revisit would have chosen. We chose card support on a demand analysis we wrote after we had already decided; the honesty is similar, the method isn't.

### 3. An independent oracle grades the agent

Faro's expected outcomes come from the policy applied to the frozen bank in code that shares nothing with the agent's tools, so a bug on either side shows as a disagreement instead of agreeing with itself. Ours are hand-written per case in the golden file (expected outcomes, facts by substring). Our scoring is deterministic too, but the expectations were written by the same person who wrote the system, looking at the same tools.

### 4. The evaluation is an order of magnitude larger and pre-registered

| | Faro | Lía |
|---|---|---|
| Held-out cases | 608 (half Portuguese), played 3 times | 33 live (10 Portuguese), played once; 120 offline |
| Split | By customer, by wording source (own vs. another model family), by time; tests enforce no leakage | By case id hash into customer profiles; messages held out from the classifier's training |
| Baseline | Same graph with a keyword router, pattern extraction, templated replies (no model) | One LLM with all tools, no critic, no gate |
| Repeatability | Three runs agree within half a point; pass^3 reported | Single run; variance unknown |
| Judge | Claude Opus 5.5, validated against 136 blind hand grades; 1 of 9 questions met the bar and only that one is used | Deferred |
| Post-freeze fixes | One, labeled, verified on the affected cases, not re-measured on the set, and said so | One (tool retry), labeled, not re-measured |
| Studies | Pre-registered (ADR-0011), with hashes of the registered text | None |

Their headline M-01 is 65% safe resolution against a 43% baseline; ours is 97% correct against 79%. The numbers are not comparable: theirs are multi-turn conversations with a scripted customer, a ceiling of 77% by policy, and 56 failures from a single misread they chose to report rather than patch away. Ours are single-turn cases on a small subset. A judge reading both would trust theirs more, and would be right to.

### 5. The model does less

Faro's graph is a workflow with model steps, not an agent loop: code chooses every node and tool; the model labels, extracts, picks a transaction, and writes words **around placeholders that code fills** (`{card.expiration}`, never the value). Outcomes are fixed sentences. A reply check rejects any digit, flag, or status the model wasn't supposed to write, and substitutes a fixed reply. We let Sonnet write the whole answer and then asked a second model (the critic) to review it. Their design removes the failure class; ours detects it sometimes, at double the model cost and latency (7.4 s vs. 4.1 s median).

### 6. The human is placed where the risk is

A block is confirmed by **the customer's button** (typed "sí" never counts), executed, then read back before it is reported. A person is involved for ambiguity, conflicting records, disputes, and anything the policy can't resolve. We routed every lost-card block through a supervisor with a 10-minute wait: safe on paper, and a product no bank would ship. We optimized the metric "0 writes without a human" instead of the customer's outcome.

### 7. Facts and claims are kept apart in the handoff

Their case file ties each verified fact to the tool call that read it, keeps the customer's own words separate, adds the flags the model never sees, and sets `urgent` when the policy requires it. Ours has the same fields (request, facts, actions, evidence, open questions) but no trace from fact to tool call.

### 8. Honesty as a deliverable

`limitations.md` lists what the evaluation can't say, what was designed and not built, the risks accepted in the scaffold, and that the dataset can no longer be downloaded so a fork can only rerun part of the checks. The README's evaluation section leads with "two things the table doesn't show." We were honest in the same way about the fraud model and the injection regex, but in buried documents, not in the README.

### 9. Engineering hygiene as evidence

Terraform for everything, `develop`/`main` with release-checked deploys (ADR-0009), merges gated on advisories (ADR-0010), toolchain pinned by layer (ADR-0008), a CHANGELOG per promotion, CodeQL, secret scanning, a SECURITY.md, a judge entry with no credentials ("Entrar como invitado"), per-user and per-day turn caps with alarms. Factored is a staffing firm; judges read a repo as a hiring signal. Our repo has tests and pinned versions, and little else of this.

## What LedgerLens did that we didn't

LedgerLens is a different shape: a two-person team on AWS's template, more product and demo polish, a smaller but iterated evaluation.

1. **It researched how the hackathon is judged** before building: the five official dimensions, past Factored winners, competitors' public repos, and a table of "what evidence a judge can see in 90 seconds" per dimension.
2. **Customer journeys, not capabilities.** It loads the customer's context at session start and predicts why they are calling; it explains *why* a payment was declined, converts foreign-currency charges, and detects when the bank's own records contradict each other. We offered six generic capabilities.
3. **It designed the judges' experience:** five judge logins, each a different customer and case type with a script of what to say and what must happen; the deck served inside the app; a read-only AWS console user; a speaker script with prepared answers to hostile questions ("didn't you overfit v12?" → "partly, yes, and here is the one case it made worse").
4. **It opened with the business:** contact-center cost, NPS, and first-contact resolution with baselines from the dataset. We had FCR and escalation rates in `DEMAND_ANALYSIS.md` and never put them in the story.
5. **Its evaluation iterated:** 10 multi-turn cases × 3 runs × 3 models × 3 prompt versions, with regressions between versions and an external judge reported with its agreement rate.
6. **Production signals a judge recognizes at once:** IaC, real identity, Cedar outside the model, guardrails, dashboards, security scans in CI.

## Where we were as good or better

Said without false modesty, and also without illusion: a judge could not see most of it, because it lived in technical documents rather than in the demo or the pitch.

- **PII handling.** We tokenize personal data before the model sees it, with a reversible encrypted vault. Faro masks card numbers; LedgerLens's guardrail "masks nothing."
- **A baseline that matches the brief's framing** (single LLM with all tools vs. the system), with 0/33 vs. 3/33 unsafe outcomes: a direct argument for the architecture.
- **Honesty about `fraud_score`.** All three entries found it is a copy of the label. Faro and we refused to learn from it; LedgerLens exposes it as "the bank's fraud engine" and its pitch calls it "a second model that checks whether you were scammed."
- **Test coverage and adversarial cases:** 201 tests, 31% Portuguese, direct and indirect injection, fault injection, expired sessions, other customers' cards.
- **Cost:** deployed for free with a hard daily spend cap; both winners used a full AWS account.
- **Teaching-oriented code:** the explicit ReAct loop, the hand-built graph, and the step-by-step docs were a learning goal, and they served it. That was never a judging criterion.

## Lessons, in order of impact

1. **Ship from day 2.** A deployed link, a one-page README, a slide skeleton, and a video outline exist before any feature is polished; every day ends with something submittable.
2. **Turn the rubric into IDs and build to them.** Cite them in docs, ADRs, and commits; make a "where a judge sees it" table per dimension.
3. **Pre-register decisions and studies.** Write the rule, commit it, then run the numbers; when the rule has to change, revise it in the open and say what each alternative would have chosen.
4. **Let the model do less.** Labels, extraction, and words around placeholders; code fills facts, fixes outcomes, and checks every reply. It is safer and cheaper than a critic model.
5. **Put the human where the risk is.** Customer confirmation for protective, reversible actions; a person for ambiguity, conflicts, and irreversible outcomes.
6. **Evaluate like an ML engineer.** Hundreds of cases, several runs, a held-out split with leakage tests, an oracle independent of the tools, a validated judge, and every failure published.
7. **Design the judges' path.** Scripted personas, a guest entry with no credentials, the deck in the app, prepared answers to the hard questions.
8. **Lead with the business, then the architecture.** Volumes, handle time, FCR, and a projected ROI labeled as a projection.
9. **Make honesty visible.** Limitations in the README, not only in a report.
10. **Treat repo hygiene as evidence:** IaC, release gates, CHANGELOG, security scanning, SECURITY.md.

## What this repository is now

A complete, working, documented card-support assistant with a solid safety model, built and finished after the deadline. As a portfolio piece it holds up; as a hackathon entry it didn't exist in time. The next competition starts with lesson 1.
