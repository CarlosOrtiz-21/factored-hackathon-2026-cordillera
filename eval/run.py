"""Live evaluation: system vs. baseline on the golden set. THIS SPENDS MONEY (Anthropic API).

    uv run python -m eval.run                          # dry run: plan + cost estimate only
    uv run python -m eval.run --yes --max-usd 3        # live subset (33 cases), both systems
    uv run python -m eval.run --yes --cases es-st-01,es-bl-01 --max-usd 0.5

Safety for the budget: nothing is called without --yes, and the run stops before a case
whose estimated cost would cross --max-usd (cases run as system+baseline pairs, so a stop
never leaves one side without the other).
Results: eval/results/live-<timestamp>/{traces,scores}.jsonl + summary.json.
"""

import argparse
import json
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from cardagent.agents.guards import load_intent_fallback
from cardagent.api.container import default_retriever
from cardagent.bank.db import BankDB
from cardagent.config import get_settings
from cardagent.llm.client import AnthropicLLM
from cardagent.llm.profiles import get_agent_configs
from cardagent.pii.recognizers import build_analyzer
from eval.baseline import run_baseline
from eval.cases import GOLDEN_VERSION, Case, FixtureFactory, load_golden
from eval.checks import score
from eval.env import open_env
from eval.metrics import breakdown, summarize
from eval.system import run_system

# Rough USD per case (mixed models); measured values replace these in the report.
ESTIMATE = {"system": 0.045, "baseline": 0.035}
RESULTS = Path("eval/results")


def select(cases: list[Case], args) -> list[Case]:
    if args.cases:
        wanted = set(args.cases.split(","))
        cases = [c for c in cases if c.id in wanted]
    elif args.subset == "live":
        cases = [c for c in cases if c.live]
    return cases[: args.limit] if args.limit else cases


def estimate(case: Case, system: str) -> float:
    return 0.0 if case.setup.session == "expired_at_entry" else ESTIMATE[system]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--systems", default="system,baseline")
    ap.add_argument("--subset", choices=["live", "all"], default="live")
    ap.add_argument("--cases", help="comma-separated case ids (overrides --subset)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--max-usd", type=float, default=3.0)
    ap.add_argument("--yes", action="store_true", help="really call the API (costs money)")
    args = ap.parse_args()

    settings = get_settings()
    systems = args.systems.split(",")
    cases = select(load_golden(), args)
    planned = sum(estimate(c, s) for c in cases for s in systems)
    models = {k: v.model for k, v in get_agent_configs().items()}
    print(f"golden {GOLDEN_VERSION}: {len(cases)} cases x {systems}")
    print(f"models {models}, prompts {settings.prompt_version}")
    print(f"estimated cost ~US${planned:.2f} (cap --max-usd {args.max_usd})")
    if not args.yes:
        print("dry run: nothing was called. Add --yes to run it for real.")
        return

    run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out = RESULTS / f"live-{run_id}"
    out.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="eval-pristine-"))
    pristine = workdir / "bank.db"
    shutil.copyfile(settings.bank_db_path, pristine)  # the real bank.db is only copied
    factory_db = BankDB(pristine)
    factory = FixtureFactory(factory_db, settings.bank_as_of_date)
    analyzer, retriever = build_analyzer(), default_retriever()
    fallback, llm = load_intent_fallback(), AnthropicLLM()

    scores, spent, stopped = {s: [] for s in systems}, 0.0, None
    with (
        (out / "traces.jsonl").open("w", encoding="utf-8") as traces_f,
        (out / "scores.jsonl").open("w", encoding="utf-8") as scores_f,
    ):
        for case in cases:
            pair = sum(estimate(case, s) for s in systems)
            if spent + pair > args.max_usd:
                stopped = f"budget cap reached before {case.id} (spent US${spent:.2f})"
                break
            fixture = factory.for_case(case)
            for system in systems:
                env = open_env(case, fixture, pristine, analyzer, retriever)
                try:
                    if system == "system":
                        trace = run_system(env, settings, llm, fallback)
                    else:
                        trace = run_baseline(env, settings, llm)
                finally:
                    env.close()
                sc = score(case, fixture, trace)
                spent += trace.cost_usd
                scores[system].append(sc)
                traces_f.write(trace.model_dump_json() + "\n")
                scores_f.write(sc.model_dump_json() + "\n")
                flag = "OK " if sc.correct else "BAD"
                unsafe = f" UNSAFE={sc.unsafe}" if sc.unsafe else ""
                print(
                    f"{flag} {system:8} {case.id:10} expected={case.expect} got={trace.outcome}"
                    f" ${trace.cost_usd:.4f}{unsafe}"
                )
    factory_db.close()
    shutil.rmtree(workdir, ignore_errors=True)

    summary = {
        "run_id": run_id,
        "kind": "live (real Anthropic API calls)",
        "golden_version": GOLDEN_VERSION,
        "models": models,
        "prompt_version": settings.prompt_version,
        "cases_planned": len(cases),
        "stopped": stopped,
        "spent_usd": round(spent, 4),
        "systems": {
            s: {
                "overall": summarize(v),
                "by_language": breakdown(v, "lang"),
                "by_category": breakdown(v, "category"),
                "by_segment": breakdown(v, "segment"),
            }
            for s, v in scores.items()
        },
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), "utf-8")
    print(f"\nspent US${spent:.4f}" + (f" — {stopped}" if stopped else ""))
    print(f"results in {out}. Next: uv run python -m eval.report")


if __name__ == "__main__":
    main()
