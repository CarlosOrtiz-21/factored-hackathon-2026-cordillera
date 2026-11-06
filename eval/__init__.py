"""Step 6: golden set + evaluation (system vs. baseline). Run as modules from the repo root:

    uv run python -m eval.offline                     # free: no API calls, all 120 cases
    uv run python -m eval.run --max-usd 3             # PAID: live subset, system + baseline
    uv run python -m eval.report                      # docs/EVAL_REPORT.md from the results

Every case runs on its own throwaway copy of data/bank.db, so the real demo database is
never modified (blocks, poisoned merchants, and sessions all happen in the copy).
"""
