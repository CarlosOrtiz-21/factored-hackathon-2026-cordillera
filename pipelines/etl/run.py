"""ETL entry point: extract -> transform -> export -> quality report, with lineage.

Usage:
    uv run python -m pipelines.etl.run                  # full pipeline
    uv run python -m pipelines.etl.run --skip-extract   # reuse data/raw as-is
    uv run python -m pipelines.etl.run --rebuild-bank   # also recreate data/bank.db

data/bank.db is only created when missing (or with --rebuild-bank). It holds RUNTIME
state -- sessions, the card_actions audit trail, blocked cards -- that a routine ETL run
must never wipe.
"""

import argparse
import hashlib
import json
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from cardagent.config import get_settings
from pipelines.etl.export import export_bank_db
from pipelines.etl.extract import MANIFEST_NAME, sync
from pipelines.etl.quality_report import write_report
from pipelines.etl.transform import connect, run_transform

REPORT_PATH = Path("docs/DATA_QUALITY.md")


def manifest_sha256(raw_dir: Path) -> str:
    path = raw_dir / MANIFEST_NAME
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skip-extract", action="store_true")
    parser.add_argument(
        "--rebuild-bank",
        action="store_true",
        help="recreate data/bank.db (DESTROYS sessions, audit trail, and demo state)",
    )
    args = parser.parse_args()

    settings = get_settings()
    run_id = f"{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
    started = datetime.now(UTC)

    if not args.skip_extract:
        sync(settings)

    con = connect(settings.warehouse_path)
    stats = run_transform(con, settings.raw_dir, run_id)
    for s in stats:
        print(
            f"{s.table:26} files={s.files:5} bronze={s.bronze_rows:9,} "
            f"quarantined={s.quarantined:6,} dups={s.duplicates_in_batch:5,} "
            f"upserted={s.upserted:9,}"
        )

    if args.rebuild_bank or not settings.bank_db_path.exists():
        bank_counts = export_bank_db(con, settings.bank_db_path)
        print(f"bank.db: {bank_counts}")
    else:
        bank_counts = {"skipped": "exists; use --rebuild-bank to recreate"}
        print("bank.db exists, kept (runtime state preserved)")

    write_report(con, REPORT_PATH, run_id)
    con.execute(
        "INSERT INTO etl_runs VALUES (?, ?, ?, ?, ?)",
        [
            run_id,
            started,
            datetime.now(UTC),
            manifest_sha256(settings.raw_dir),
            json.dumps({"tables": [asdict(s) for s in stats], "bank_db": bank_counts}),
        ],
    )
    con.close()
    print(f"run {run_id} done; report at {REPORT_PATH}")


if __name__ == "__main__":
    main()
