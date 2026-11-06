"""Transform: bronze -> silver -> gold in DuckDB, incremental and idempotent.

Medallion layers:
- bronze_<t>:     raw rows exactly as they arrived (all VARCHAR) + lineage columns.
- silver_<t>:     typed, normalized, contract-valid, one row per primary key.
  quarantine_<t>: rows that broke the contract, with the reasons. Never silently dropped.
- gold_*:         business tables built from silver for the app and the analysis.

Incremental by *file*: `etl_processed_files` records every file (path + size + mtime) a
run has loaded. The next run only reads new or changed files, so a partition that
arrives late -- even months late -- is picked up, and re-running loads nothing twice.
Upserts by primary key make a re-delivered file replace, not duplicate, its rows.
"""

from dataclasses import dataclass
from pathlib import Path

import duckdb

from pipelines.etl.contracts import CONTRACTS, LOAD_ORDER, TableContract

HIVE_COLUMNS = ("year", "month", "day")


@dataclass
class LoadStats:
    table: str
    files: int = 0
    bronze_rows: int = 0
    quarantined: int = 0
    duplicates_in_batch: int = 0
    upserted: int = 0
    unexpected_columns: tuple[str, ...] = ()
    missing_columns: tuple[str, ...] = ()


def q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def connect(path: Path | str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(path))
    con.execute("""
        CREATE TABLE IF NOT EXISTS etl_processed_files (
            table_name VARCHAR, path VARCHAR, size BIGINT, mtime_ns BIGINT, run_id VARCHAR,
            PRIMARY KEY (table_name, path)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS etl_runs (
            run_id VARCHAR PRIMARY KEY, started_at TIMESTAMP, finished_at TIMESTAMP,
            source_manifest_sha256 VARCHAR, stats JSON
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS etl_schema_drift (
            run_id VARCHAR, table_name VARCHAR, column_name VARCHAR, kind VARCHAR
        )
    """)
    return con


# --- file discovery ---------------------------------------------------------------


def source_files(contract: TableContract, raw_dir: Path) -> list[Path]:
    if contract.partitioned:
        return sorted((raw_dir / contract.name).rglob("*.csv"))
    path = raw_dir / f"{contract.name}.csv"
    return [path] if path.exists() else []


def new_files(con, contract: TableContract, raw_dir: Path) -> list[Path]:
    seen = {
        path: (size, mtime)
        for path, size, mtime in con.execute(
            "SELECT path, size, mtime_ns FROM etl_processed_files WHERE table_name = ?",
            [contract.name],
        ).fetchall()
    }
    todo = []
    for f in source_files(contract, raw_dir):
        st = f.stat()
        if seen.get(rel(f, raw_dir)) != (st.st_size, st.st_mtime_ns):
            todo.append(f)
    return todo


def rel(path: Path, raw_dir: Path) -> str:
    return path.relative_to(raw_dir).as_posix()


# --- SQL builders -----------------------------------------------------------------


def _normalized(contract: TableContract, col: str) -> str:
    expr = f"trim({q(col)})"
    mapping = contract.normalize.get(col)
    if mapping:
        cases = " ".join(f"WHEN {lit(k)} THEN {lit(v)}" for k, v in mapping.items())
        expr = f"CASE {expr} {cases} ELSE {expr} END"
    return expr


def _typed(contract: TableContract, col: str) -> str:
    typ = contract.columns[col]
    raw = _normalized(contract, col)
    if typ == "VARCHAR":
        return raw
    if typ == "INTEGER":  # the files write integers as '250.0'
        return f"TRY_CAST(TRY_CAST({raw} AS DOUBLE) AS INTEGER)"
    return f"TRY_CAST({raw} AS {typ})"


def _reasons(contract: TableContract) -> str:
    checks = []
    for col, typ in contract.columns.items():
        raw = q(col)
        if col in contract.required:
            checks.append(f"CASE WHEN {raw} IS NULL THEN 'missing:{col}' END")
        if typ != "VARCHAR":
            checks.append(
                f"CASE WHEN {raw} IS NOT NULL AND {_typed(contract, col)} IS NULL "
                f"THEN 'bad_type:{col}' END"
            )
        if col in contract.enums:
            allowed = ", ".join(lit(v) for v in sorted(contract.enums[col]))
            checks.append(
                f"CASE WHEN {raw} IS NOT NULL AND {_typed(contract, col)} NOT IN ({allowed}) "
                f"THEN 'bad_value:{col}' END"
            )
    return f"concat_ws(';', {', '.join(checks)})"


def _pk_expr(contract: TableContract) -> str:
    return "concat_ws('|', " + ", ".join(q(c) for c in contract.primary_key) + ")"


# --- load one table ---------------------------------------------------------------


def load_table(con, contract: TableContract, raw_dir: Path, run_id: str) -> LoadStats:
    name = contract.name
    stats = LoadStats(table=name)
    files = new_files(con, contract, raw_dir)
    stats.files = len(files)
    if not files:
        return stats

    # 1) Read the batch as raw text. union_by_name tolerates schema evolution
    #    (a column added in later files is NULL for earlier ones).
    file_list = "[" + ", ".join(lit(f.as_posix()) for f in files) + "]"
    raw_prefix = raw_dir.resolve().as_posix().rstrip("/") + "/"
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE batch AS
        SELECT * EXCLUDE (filename),
               replace(replace(filename, '\\', '/'), {lit(raw_prefix)}, '') AS _source_file
        FROM read_csv({file_list}, all_varchar = true, union_by_name = true,
                      hive_partitioning = {str(contract.partitioned).lower()}, filename = true)
    """)

    # 2) Schema drift: record unexpected/missing columns; add missing ones as NULL.
    present = [r[0] for r in con.execute("DESCRIBE batch").fetchall()]
    known = set(contract.columns) | set(HIVE_COLUMNS) | {"_source_file"}
    stats.unexpected_columns = tuple(c for c in present if c not in known)
    stats.missing_columns = tuple(c for c in contract.columns if c not in present)
    for kind, cols in (
        ("unexpected", stats.unexpected_columns),
        ("missing", stats.missing_columns),
    ):
        for col in cols:
            con.execute(
                "INSERT INTO etl_schema_drift VALUES (?, ?, ?, ?)", [run_id, name, col, kind]
            )
    for col in stats.missing_columns:
        con.execute(f"ALTER TABLE batch ADD COLUMN {q(col)} VARCHAR")

    # 3) Bronze: append the raw batch with lineage.
    raw_cols = ", ".join(q(c) for c in contract.columns)
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS bronze_{name} AS
        SELECT {raw_cols}, _source_file, ''::VARCHAR AS _run_id, now() AS _ingested_at
        FROM batch LIMIT 0
    """)
    con.execute(f"""
        INSERT INTO bronze_{name}
        SELECT {raw_cols}, _source_file, {lit(run_id)}, now() FROM batch
    """)
    stats.bronze_rows = con.execute("SELECT count(*) FROM batch").fetchone()[0]

    # 4) Contract checks: every row gets a (possibly empty) list of violations.
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE checked AS
        SELECT *, {_reasons(contract)} AS _reasons FROM batch
    """)
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS quarantine_{name} AS
        SELECT {raw_cols}, _source_file, _reasons, ''::VARCHAR AS _run_id FROM checked LIMIT 0
    """)
    con.execute(f"""
        INSERT INTO quarantine_{name}
        SELECT {raw_cols}, _source_file, _reasons, {lit(run_id)} FROM checked
        WHERE _reasons <> ''
    """)
    stats.quarantined = con.execute("SELECT count(*) FROM checked WHERE _reasons <> ''").fetchone()[
        0
    ]

    # 5) Typed rows, deduplicated within the batch: highest version wins.
    typed_cols = ", ".join(f"{_typed(contract, c)} AS {q(c)}" for c in contract.columns)
    order = f"{q(contract.version_column)} DESC NULLS LAST, _source_file DESC"
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE typed AS
        SELECT {typed_cols}, _source_file FROM checked WHERE _reasons = ''
    """)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE valid AS
        SELECT *, {_pk_expr(contract)} AS _pk FROM typed
        QUALIFY row_number() OVER (PARTITION BY {_pk_expr(contract)} ORDER BY {order}) = 1
    """)
    typed_n = con.execute("SELECT count(*) FROM typed").fetchone()[0]
    stats.upserted = con.execute("SELECT count(*) FROM valid").fetchone()[0]
    stats.duplicates_in_batch = typed_n - stats.upserted

    # 6) Upsert into silver: merge with existing versions of the same keys, keep the latest.
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS silver_{name} AS
        SELECT *, now() AS _ingested_at, ''::VARCHAR AS _run_id, false AS _is_orphan
        FROM valid LIMIT 0
    """)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE merged AS
        SELECT * FROM (
            SELECT * FROM silver_{name} WHERE _pk IN (SELECT _pk FROM valid)
            UNION ALL BY NAME
            SELECT *, now() AS _ingested_at, {lit(run_id)} AS _run_id, false AS _is_orphan
            FROM valid
        )
        QUALIFY row_number() OVER (PARTITION BY _pk ORDER BY {order}) = 1
    """)
    con.execute(f"DELETE FROM silver_{name} WHERE _pk IN (SELECT _pk FROM valid)")
    con.execute(f"INSERT INTO silver_{name} BY NAME SELECT * FROM merged")

    # 7) Remember which files this run consumed (only after the load succeeded).
    for f in files:
        st = f.stat()
        con.execute(
            "INSERT OR REPLACE INTO etl_processed_files VALUES (?, ?, ?, ?, ?)",
            [name, rel(f, raw_dir), st.st_size, st.st_mtime_ns, run_id],
        )
    return stats


def table_exists(con, name: str) -> bool:
    return bool(
        con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]
        ).fetchone()[0]
    )


def flag_orphans(con) -> None:
    """Mark (don't delete) rows whose foreign keys point to a missing parent."""
    for name in LOAD_ORDER:
        contract = CONTRACTS[name]
        if not contract.foreign_keys or not table_exists(con, f"silver_{name}"):
            continue
        conditions = []
        for col, parent in contract.foreign_keys.items():
            if not table_exists(con, f"silver_{parent}"):
                continue
            parent_pk = q(CONTRACTS[parent].primary_key[0])
            conditions.append(
                f"({q(col)} IS NOT NULL AND NOT EXISTS (SELECT 1 FROM silver_{parent} p "
                f"WHERE p.{parent_pk} = silver_{name}.{q(col)}))"
            )
        if conditions:
            con.execute(f"UPDATE silver_{name} SET _is_orphan = {' OR '.join(conditions)}")


# --- gold -------------------------------------------------------------------------

CARD_TYPES = "('Tarjeta Crédito', 'Tarjeta Débito')"


def build_gold(con) -> None:
    """Rebuilt from silver on every run: cheap, and always consistent with silver."""
    has = lambda *tables: all(table_exists(con, f"silver_{t}") for t in tables)  # noqa: E731
    if not has("products"):
        return
    con.execute(f"""
        CREATE OR REPLACE TABLE gold_card_products AS
        SELECT * EXCLUDE (_pk, _source_file, _ingested_at, _run_id, _is_orphan),
               CASE product_type WHEN 'Tarjeta Crédito' THEN 'credit' ELSE 'debit' END
                   AS card_kind,
               right(product_number, 4) AS last4
        FROM silver_products
        WHERE product_type IN {CARD_TYPES} AND NOT _is_orphan
    """)
    if has("transactions"):
        con.execute("""
        CREATE OR REPLACE TABLE gold_card_transactions AS
        SELECT t.* EXCLUDE (_pk, _source_file, _ingested_at, _run_id, _is_orphan), c.card_kind
        FROM silver_transactions t
        JOIN gold_card_products c USING (product_id)
        WHERE NOT t._is_orphan
    """)
    if not has("call_center_interactions", "complaints"):
        return
    # Which interactions mention a card product: the data-backed demand signal, since
    # contact_reason only has 6 coarse categories.
    con.execute(f"""
        CREATE OR REPLACE TABLE gold_interactions AS
        WITH mentions AS (
            SELECT m.interaction_id,
                   bool_or(p.product_type IN {CARD_TYPES}) AS mentions_card
            FROM (
                SELECT interaction_id, trim(unnest(string_split(mentioned_products, ','))) AS pid
                FROM silver_call_center_interactions
                WHERE mentioned_products IS NOT NULL
            ) m
            JOIN silver_products p ON p.product_id = m.pid
            GROUP BY 1
        )
        SELECT i.* EXCLUDE (_pk, _source_file, _ingested_at, _run_id),
               coalesce(mentions.mentions_card, false) AS mentions_card
        FROM silver_call_center_interactions i
        LEFT JOIN mentions USING (interaction_id)
    """)
    con.execute(f"""
        CREATE OR REPLACE TABLE gold_complaints AS
        SELECT c.* EXCLUDE (_pk, _source_file, _ingested_at, _run_id),
               p.product_type AS affected_product_type,
               coalesce(p.product_type IN {CARD_TYPES}, false) AS affects_card
        FROM silver_complaints c
        LEFT JOIN silver_products p ON p.product_id = c.affected_product_id
    """)


def run_transform(con, raw_dir: Path, run_id: str) -> list[LoadStats]:
    stats = [load_table(con, CONTRACTS[name], raw_dir, run_id) for name in LOAD_ORDER]
    flag_orphans(con)
    build_gold(con)
    return stats
