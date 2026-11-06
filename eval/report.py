"""Writes docs/EVAL_REPORT.md from eval/results/offline.json and the latest live run.

uv run python -m eval.report
uv run python -m eval.report --live eval/results/live-20261105-200000
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from eval.cases import load_golden

RESULTS = Path("eval/results")
REPORT = Path("docs/EVAL_REPORT.md")


def pct(r: dict | None) -> str:
    if not r or r.get("rate") is None:
        return "—"
    return f"{r['rate'] * 100:.0f}% ({r['n']}/{r['of']})"


def latest_live() -> Path | None:
    runs = sorted(p for p in RESULTS.glob("live-*") if (p / "summary.json").exists())
    return runs[-1] if runs else None


def golden_section() -> list[str]:
    cases = load_golden()
    cats: dict[str, list[int]] = {}
    for c in cases:
        row = cats.setdefault(c.category, [0, 0, 0])
        row[0] += 1
        row[1] += c.lang == "pt"
        row[2] += c.live
    lines = [
        "## Golden set",
        "",
        f"`eval/golden/golden_v1.jsonl`: **{len(cases)} cases**, "
        f"{sum(c.lang == 'pt' for c in cases)} in Portuguese "
        f"({sum(c.lang == 'pt' for c in cases) / len(cases):.0%}). Written and labeled by the "
        "team (team-generated), held out from the intent classifier's training data. Customers "
        "are real rows of the synthetic LATAM Bank dataset, picked per case by profile.",
        "",
        "| Category | Cases | PT | In live subset |",
        "|---|---|---|---|",
    ]
    lines += [f"| {k} | {v[0]} | {v[1]} | {v[2]} |" for k, v in sorted(cats.items())]
    return lines + [""]


def offline_section(o: dict) -> list[str]:
    inj, fb = o["injection_regex"], o["intent_fallback"]
    lines = [
        "## Offline evaluation (no API calls, all cases)",
        "",
        "| Component | Metric | Result |",
        "|---|---|---|",
        f"| Fixtures | cases resolved to a real customer | {pct(o['fixtures_resolved'])} |",
        f"| PII guard | full card numbers masked before any LLM | "
        f"{pct(o['pii_card_numbers_masked'])} |",
        f"| Injection regex (backup layer) | recall on direct injections | "
        f"{pct(inj['recall_direct'])} |",
        f"| Injection regex | false positives on non-injection cases | "
        f"{pct(inj['false_positives'])} |",
        f"| Intent classifier (triage fallback) | coverage above threshold | "
        f"{pct(fb['coverage'])} |",
        f"| Intent classifier | accuracy when it answers | {pct(fb['accuracy_when_accepted'])} |",
        f"| Language heuristic (fallback) | correct language | {pct(o['language_heuristic'])} |",
        "",
        "Intent fallback by language: "
        + ", ".join(
            f"{lang}: coverage {pct(s['coverage'])}, accuracy {pct(s['accuracy_when_accepted'])}"
            for lang, s in fb["by_language"].items()
        )
        + ".",
        "",
    ]
    if inj["recall_direct"]["rate"] is not None and inj["recall_direct"]["rate"] < 0.8:
        lines += [
            "> **Finding:** the injection regex is a weak backup. The LLM triage is the primary "
            "detector, and the live run measures the whole system. The regex must not be tuned on "
            "this golden set (that would leak the test into the rule); improve it on a separate "
            "dev set and re-measure here.",
            "",
        ]
    lines += [
        "> **Injection regex, revised after the first measurement.** Golden v1 first measured "
        "2/10 on direct injections. The regex was rewritten by attack family and tuned on a "
        "separate dev set (`datasets/injection_dev_v1.csv`: 34 attacks, 32 tricky benign "
        "messages; now 34/34 and 0 false positives, plus 0/505 on the intent training "
        "utterances). The authors had seen which golden cases failed, so the golden figure "
        "above is **optimistic**; a fresh golden v2 would give an unbiased estimate.",
        "",
    ]
    return lines


METRICS = [
    ("Correct (outcome + facts + safe)", "correct"),
    ("Safe automated resolution", "safe_automated_resolution"),
    ("Automation attempted", "automation_attempted"),
    ("Containment (not escalated)", "containment"),
    ("Missed escalations", "missed_escalations"),
    ("Unnecessary escalations", "unnecessary_escalations"),
    ("**Unsafe outcomes (n/N)**", "unsafe_outcomes"),
    ("Writes approved by a human (HITL coverage)", "hitl_coverage"),
    ("Injection resistance", "injection_resistance"),
    ("Correct abstention (policy not in corpus)", "correct_abstention"),
]


def live_section(path: Path) -> list[str]:
    s = json.loads((path / "summary.json").read_text(encoding="utf-8"))
    systems = list(s["systems"])
    head = "| Metric | " + " | ".join(systems) + " |"
    sep = "|---|" + "---|" * len(systems)
    lines = [
        "## Live evaluation (real Anthropic API calls)",
        "",
        f"Run `{s['run_id']}` · golden {s['golden_version']} · "
        f"{s['systems'][systems[0]]['overall']['cases']} cases per system · "
        f"prompts {s['prompt_version']} · models {s['models']} · spent US${s['spent_usd']}"
        + (f" · **stopped early:** {s['stopped']}" if s["stopped"] else ""),
        "",
        head,
        sep,
    ]
    for label, key in METRICS:
        lines.append(
            f"| {label} | "
            + " | ".join(pct(s["systems"][x]["overall"][key]) for x in systems)
            + " |"
        )
    lat = [s["systems"][x]["overall"]["latency_ms"] for x in systems]
    cost = [s["systems"][x]["overall"]["cost_usd"] for x in systems]
    lines += [
        "| Latency p50 / p95 (ms) | " + " | ".join(f"{v['p50']} / {v['p95']}" for v in lat) + " |",
        "| Cost total (USD) | " + " | ".join(str(c["total"]) for c in cost) + " |",
        "| Cost per case (USD) | " + " | ".join(str(c["per_case"]) for c in cost) + " |",
        "| Cost per safe resolution (USD) | "
        + " | ".join(str(c["per_safe_resolution"]) for c in cost)
        + " |",
        "",
        "Unsafe flags by type: "
        + "; ".join(
            f"**{x}**: {s['systems'][x]['overall']['unsafe_by_type'] or 'none'}" for x in systems
        ),
        "",
    ]
    for dim, title in (
        ("by_language", "language"),
        ("by_segment", "segment"),
        ("by_category", "category"),
    ):
        lines += [
            f"### By {title}",
            "",
            "| "
            + title.capitalize()
            + " | "
            + " | ".join(f"{x}: correct / unsafe" for x in systems)
            + " |",
            "|---|" + "---|" * len(systems),
        ]
        groups = sorted(set().union(*(s["systems"][x][dim] for x in systems)))
        for g in groups:
            cells = []
            for x in systems:
                m = s["systems"][x][dim].get(g)
                cells.append(f"{pct(m['correct'])} / {pct(m['unsafe_outcomes'])}" if m else "—")
            lines.append(f"| {g} | " + " | ".join(cells) + " |")
        lines.append("")

    failures = [
        json.loads(line)
        for line in (path / "scores.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    bad = [f for f in failures if not f["correct"]]
    notes = path / "notes.md"  # manual reading of the traces, kept across regenerations
    if notes.exists():
        lines += [notes.read_text(encoding="utf-8").strip(), ""]
    lines += [
        "### Cases not handled correctly",
        "",
        "| System | Case | Expected | Got | Unsafe | Notes |",
        "|---|---|---|---|---|---|",
    ]
    for f in bad:
        notes = ", ".join(
            f["quality"]
            + (["facts missing"] if f["facts_ok"] is False else [])
            + (["no question asked"] if f["asks_question"] is False else [])
        )
        lines.append(
            f"| {f['system']} | {f['case_id']} | {', '.join(f['expected'])} | {f['actual']} | "
            f"{', '.join(f['unsafe']) or '—'} | {notes or '—'} |"
        )
    return lines + [""]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", type=Path, help="a live-* results folder (default: the latest)")
    args = ap.parse_args()
    offline = RESULTS / "offline.json"
    live = args.live or latest_live()

    lines = [
        "# Evaluation report",
        "",
        f"> Generated by `uv run python -m eval.report` on {datetime.now(UTC):%Y-%m-%d %H:%M} UTC. "
        "Banco Cordillera is **fictional**; customers and transactions come from the "
        "**synthetic** LATAM Bank dataset, and the policy corpus is **synthetic**. Offline "
        "and live results are labeled as such. No projected savings are claimed.",
        "",
        "**Systems.** *system*: the multi-agent graph (triage → executor/RAG → critic → human "
        "approval → deterministic execution → verification). *baseline*: one LLM (the executor "
        "model) with all tools including `block_card`, a policy search, and escalation; no "
        "critic, no guards, no human approval. Both share the channel (session check, PII "
        "guard, masked output) and the bank's own ownership checks.",
        "",
        *golden_section(),
    ]
    if offline.exists():
        lines += offline_section(json.loads(offline.read_text(encoding="utf-8")))
    else:
        lines += ["## Offline evaluation", "", "Not run yet: `uv run python -m eval.offline`.", ""]
    if live:
        lines += live_section(live)
    else:
        lines += [
            "## Live evaluation (real Anthropic API calls)",
            "",
            "Not run yet. It costs money: `uv run python -m eval.run` shows the plan and the "
            "estimate; `--yes --max-usd 3` runs it with a hard budget cap.",
            "",
        ]
    lines += [
        "## Method and limitations",
        "",
        "- **Scoring is deterministic** (`eval/checks.py`): outcome from the system's route or "
        "the baseline's tool calls; facts by substring (card last 4, merchants, policy "
        "numbers); unsafe flags from the bank's `card_actions` table, reply text, and tool "
        "outputs. No LLM judge (deferred: it needs ~30 human ratings to validate).",
        "- **Baseline outcomes for answer/abstain/refuse are self-reported** through its "
        "`final_answer` tool; writes and escalations are observed.",
        "- **Human approval is simulated**: the case's scripted decision, by `auditor-eval`.",
        "- **Small live subset, one run**: budget decision (minimal API spend). No variance "
        "estimate; differences of one or two cases are within noise.",
        "- Fact checks are proxies: a correct answer worded differently can miss a substring "
        "(false negative); every 'facts missing' row is listed above for manual review.",
        "- Expired sessions are refused by the channel for both systems (the API's 401), so "
        "those cases test the channel, not the agents.",
        "",
    ]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"written {REPORT}" + (f" (live: {live})" if live else " (offline only)"))


if __name__ == "__main__":
    main()
