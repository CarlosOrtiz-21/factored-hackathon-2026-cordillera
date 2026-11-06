"""Step 1b tests: ETL guarantees, on a tiny SYNTHETIC TEST FIXTURE (not the real dataset).

The fixture is written to a temp dir with the same layout as data/raw: snapshot CSVs for
dimensions and hive-partitioned daily CSVs for facts.
"""

import csv
import os
import sqlite3
from pathlib import Path

import pytest

from pipelines.etl.contracts import CONTRACTS
from pipelines.etl.export import export_bank_db
from pipelines.etl.extract import SourceObject, plan_downloads
from pipelines.etl.transform import connect, run_transform

DEFAULTS = {
    "VARCHAR": "x",
    "INTEGER": "1.0",  # the real files write integers like this
    "DOUBLE": "1.5",
    "DATE": "2024-01-01",
    "TIMESTAMP": "2024-01-01 10:00:00",
    "BOOLEAN": "False",
}


def row(table: str, **overrides) -> dict[str, str]:
    """A contract-valid row with sensible defaults, plus overrides."""
    contract = CONTRACTS[table]
    values = {}
    for col, typ in contract.columns.items():
        if col in contract.enums:
            values[col] = sorted(contract.enums[col])[0]
        else:
            values[col] = DEFAULTS.get(typ, "1.00")  # DECIMAL
    values.update(overrides)
    return values


def write_csv(path: Path, rows: list[dict], extra_cols: tuple[str, ...] = ()) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0].keys()) + list(extra_cols)
    # utf-8-sig: the real files start with a BOM, so the fixture does too.
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            writer.writerow({**{c: "" for c in extra_cols}, **{k: v for k, v in r.items()}})


def partition(raw: Path, table: str, day: str) -> Path:
    y, m, d = day.split("-")
    return raw / table / f"year={y}" / f"month={m}" / f"day={d}" / f"{table}_{y}{m}{d}.csv"


@pytest.fixture
def raw(tmp_path) -> Path:
    raw = tmp_path / "raw"
    write_csv(
        raw / "customers.csv",
        [
            row("customers", customer_id="C1", customer_status="Active", segment="Basic"),
            row("customers", customer_id="C2", customer_status="Active"),
        ],
    )
    write_csv(
        raw / "products.csv",
        [
            row(
                "products",
                product_id="P1",
                customer_id="C1",
                product_type="Tarjeta Crédito",
                product_number="4028620856924060",
            ),
            row("products", product_id="P2", customer_id="C9", product_type="Tarjeta Débito"),
        ],
    )
    write_csv(
        partition(raw, "transactions", "2024-01-02"),
        [
            row(
                "transactions",
                transaction_id="T1",
                product_id="P1",
                customer_id="C1",
                process_date="2024-01-02",
            ),
            row(
                "transactions",
                transaction_id="T1",
                product_id="P1",
                customer_id="C1",
                process_date="2024-01-02",
            ),  # duplicate key
            row(
                "transactions", transaction_id="T2", product_id="P1", customer_id="C1", amount="abc"
            ),  # bad type
            row("transactions", transaction_id="T3", product_id="P1", customer_id=""),  # missing
            row(
                "transactions",
                transaction_id="T4",
                product_id="P1",
                customer_id="C1",
                transaction_country="Mexico",
            ),  # to normalize
        ],
    )
    return raw


def run(raw: Path, tmp_path: Path, run_id: str):
    con = connect(tmp_path / "wh.duckdb")
    stats = {s.table: s for s in run_transform(con, raw, run_id)}
    return con, stats


def scalar(con, sql: str):
    return con.execute(sql).fetchone()[0]


def test_first_load_applies_contracts(raw, tmp_path):
    con, stats = run(raw, tmp_path, "r1")
    tx = stats["transactions"]
    assert (tx.bronze_rows, tx.quarantined, tx.duplicates_in_batch, tx.upserted) == (5, 2, 1, 2)
    assert scalar(con, "SELECT count(*) FROM silver_transactions") == 2

    reasons = dict(
        con.execute("SELECT transaction_id, _reasons FROM quarantine_transactions").fetchall()
    )
    assert reasons["T2"] == "bad_type:amount"
    assert reasons["T3"] == "missing:customer_id"

    assert (
        scalar(
            con, "SELECT transaction_country FROM silver_transactions WHERE transaction_id = 'T4'"
        )
        == "México"
    )
    # typed columns, including '1.0' -> INTEGER
    assert scalar(con, "SELECT typeof(days_past_due) FROM silver_products LIMIT 1") == "INTEGER"


def test_orphans_are_flagged_not_dropped(raw, tmp_path):
    con, _ = run(raw, tmp_path, "r1")
    orphans = con.execute("SELECT product_id FROM silver_products WHERE _is_orphan").fetchall()
    assert orphans == [("P2",)]  # C9 doesn't exist
    # gold only exposes non-orphan cards
    assert scalar(con, "SELECT count(*) FROM gold_card_products") == 1


def test_rerun_is_idempotent(raw, tmp_path):
    con, _ = run(raw, tmp_path, "r1")
    con.close()
    con, stats = run(raw, tmp_path, "r2")
    assert all(s.files == 0 for s in stats.values())
    assert scalar(con, "SELECT count(*) FROM silver_transactions") == 2
    assert scalar(con, "SELECT count(*) FROM bronze_transactions") == 5


def test_late_arrival_is_loaded_without_duplicates(raw, tmp_path):
    con, _ = run(raw, tmp_path, "r1")
    con.close()
    # A partition for an OLDER date arrives after the first run; it re-delivers T1
    # and brings a new transaction T5.
    write_csv(
        partition(raw, "transactions", "2023-12-15"),
        [
            row(
                "transactions",
                transaction_id="T5",
                product_id="P1",
                customer_id="C1",
                process_date="2023-12-15",
            ),
            row(
                "transactions",
                transaction_id="T1",
                product_id="P1",
                customer_id="C1",
                process_date="2023-12-15",
            ),
        ],
    )
    con, stats = run(raw, tmp_path, "r2")
    assert stats["transactions"].files == 1
    ids = [
        r[0]
        for r in con.execute("SELECT transaction_id FROM silver_transactions ORDER BY 1").fetchall()
    ]
    assert ids == ["T1", "T4", "T5"]
    # T1 keeps the most recent version (process_date 2024-01-02, not the late 2023 copy)
    assert (
        str(scalar(con, "SELECT process_date FROM silver_transactions WHERE transaction_id = 'T1'"))
        == "2024-01-02"
    )


def test_changed_snapshot_updates_rows_in_place(raw, tmp_path):
    con, _ = run(raw, tmp_path, "r1")
    con.close()
    path = raw / "customers.csv"
    write_csv(
        path,
        [
            row(
                "customers", customer_id="C1", segment="Premium", last_updated="2024-02-01 00:00:00"
            ),
            row("customers", customer_id="C2"),
        ],
    )
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 10**9))
    con, stats = run(raw, tmp_path, "r2")
    assert stats["customers"].files == 1
    assert scalar(con, "SELECT count(*) FROM silver_customers") == 2
    assert scalar(con, "SELECT segment FROM silver_customers WHERE customer_id = 'C1'") == "Premium"


def test_schema_drift_is_recorded(raw, tmp_path):
    write_csv(
        partition(raw, "transactions", "2024-01-03"),
        [row("transactions", transaction_id="T9", product_id="P1", customer_id="C1")],
        extra_cols=("loyalty_points",),
    )
    con, stats = run(raw, tmp_path, "r1")
    assert stats["transactions"].unexpected_columns == ("loyalty_points",)
    assert con.execute("SELECT column_name, kind FROM etl_schema_drift").fetchall() == [
        ("loyalty_points", "unexpected")
    ]


def test_bank_db_export_is_deterministic(raw, tmp_path):
    con, _ = run(raw, tmp_path, "r1")
    counts_a = export_bank_db(con, tmp_path / "a.db")
    counts_b = export_bank_db(con, tmp_path / "b.db")
    assert counts_a == counts_b == {"customers": 1, "cards": 1, "card_transactions": 2}
    with sqlite3.connect(tmp_path / "a.db") as db:
        assert db.execute("SELECT last4, card_kind FROM cards").fetchone() == ("4060", "credit")


def test_extract_only_downloads_new_or_changed(tmp_path):
    raw = tmp_path / "raw"
    (raw / "customers.csv").parent.mkdir(parents=True)
    (raw / "customers.csv").write_text("x")
    objs = [
        SourceObject("data/customers.csv", 1, "etag-same", "t"),
        SourceObject("data/products.csv", 1, "etag-new", "t"),
    ]
    previous = {
        "data/customers.csv": {"etag": "etag-same"},
        "data/products.csv": {"etag": "etag-old"},
    }
    todo = plan_downloads(objs, previous, raw, "data/")
    assert [o.key for o in todo] == ["data/products.csv"]
