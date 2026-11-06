"""Data-quality report: measured facts about the dataset, written to docs/DATA_QUALITY.md.

Everything here is computed from the warehouse, so the report is reproducible and
can be regenerated after every run.
"""

from datetime import UTC, datetime
from pathlib import Path

from pipelines.etl.contracts import CONTRACTS, LOAD_ORDER
from pipelines.etl.transform import q, table_exists

DATASET_END = "2026-06-17"


def luhn_ok(number: str) -> bool:
    digits = [int(c) for c in number if c.isdigit()][::-1]
    total = sum(
        d if i % 2 == 0 else (d * 2 - 9 if d * 2 > 9 else d * 2) for i, d in enumerate(digits)
    )
    return bool(digits) and total % 10 == 0


def _pct(part: float, whole: float) -> str:
    return f"{100 * part / whole:.2f}%" if whole else "n/a"


def _one(con, sql: str):
    return con.execute(sql).fetchone()[0]


def table_summary(con) -> list[str]:
    lines = [
        "| Table | Expected (dictionary) | Bronze rows | Silver rows | Quarantined "
        "| Duplicate keys removed | Orphans | Latest version |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for name in LOAD_ORDER:
        c = CONTRACTS[name]
        if not table_exists(con, f"silver_{name}"):
            continue
        bronze = _one(con, f"SELECT count(*) FROM bronze_{name}")
        silver = _one(con, f"SELECT count(*) FROM silver_{name}")
        quarantined = _one(con, f"SELECT count(*) FROM quarantine_{name}")
        orphans = _one(con, f"SELECT count(*) FROM silver_{name} WHERE _is_orphan")
        latest = _one(con, f"SELECT max({q(c.version_column)}) FROM silver_{name}")
        lines.append(
            f"| {name} | {c.expected_rows or 0:,} | {bronze:,} | {silver:,} | {quarantined:,} "
            f"| {bronze - quarantined - silver:,} | {orphans:,} ({_pct(orphans, silver)}) "
            f"| {latest} |"
        )
    return lines


def null_rates(con) -> list[str]:
    lines = ["| Table | Column | Null rate | Required? |", "|---|---|---:|---|"]
    for name in LOAD_ORDER:
        c = CONTRACTS[name]
        if not table_exists(con, f"silver_{name}"):
            continue
        exprs = ", ".join(f"avg(({q(col)} IS NULL)::INT)" for col in c.columns)
        rates = con.execute(f"SELECT {exprs} FROM silver_{name}").fetchone()
        for col, rate in zip(c.columns, rates, strict=True):
            if rate and rate > 0:
                lines.append(
                    f"| {name} | {col} | {100 * rate:.1f}% | {'yes' if col in c.required else ''} |"
                )
    return lines


def quarantine_reasons(con) -> list[str]:
    lines = ["| Table | Reason | Rows |", "|---|---|---:|"]
    for name in LOAD_ORDER:
        if not table_exists(con, f"quarantine_{name}"):
            continue
        rows = con.execute(f"""
            SELECT reason, count(*) FROM (
                SELECT unnest(string_split(_reasons, ';')) AS reason FROM quarantine_{name}
            ) GROUP BY 1 ORDER BY 2 DESC LIMIT 8
        """).fetchall()
        lines += [f"| {name} | `{reason}` | {n:,} |" for reason, n in rows]
    return lines


def late_arrivals(con) -> list[str]:
    lines = [
        "| Table | Rows where event date ≠ process_date | Max lag (days) |",
        "|---|---:|---:|",
    ]
    for name in LOAD_ORDER:
        c = CONTRACTS[name]
        if not c.event_column or not table_exists(con, f"silver_{name}"):
            continue
        total, off, max_lag = con.execute(f"""
            SELECT count(*),
                   count(*) FILTER (WHERE {q(c.event_column)}::DATE <> process_date),
                   max(abs(date_diff('day', {q(c.event_column)}::DATE, process_date)))
            FROM silver_{name}
        """).fetchone()
        lines.append(f"| {name} | {off:,} ({_pct(off, total)}) | {max_lag} |")
    return lines


def findings(con) -> list[str]:
    """Dataset-specific facts that shaped design decisions (docs/ARCHITECTURE.md)."""
    out = []
    if table_exists(con, "silver_call_center_interactions"):
        same = _one(
            con,
            """SELECT avg((contact_reason = reason_category)::INT)
                            FROM silver_call_center_interactions""",
        )
        cats = _one(
            con, "SELECT count(DISTINCT contact_reason) FROM silver_call_center_interactions"
        )
        out.append(
            f"- `contact_reason` equals `reason_category` in {100 * same:.1f}% of interactions "
            f"({cats} distinct values). It is too coarse to serve as a card-intent label."
        )
        if table_exists(con, "silver_products"):
            mentions, resolvable = con.execute("""
                WITH m AS (
                    SELECT trim(unnest(string_split(mentioned_products, ','))) AS pid
                    FROM silver_call_center_interactions WHERE mentioned_products IS NOT NULL
                )
                SELECT count(*), count(p.product_id)
                FROM m LEFT JOIN silver_products p ON p.product_id = m.pid""").fetchone()
            out.append(
                f"- `mentioned_products`: only {_pct(resolvable, mentions)} of {mentions:,} "
                "mentioned product IDs exist in `products` (a broken link in the source), so "
                "contacts can't be attributed to cards through it."
            )
    if table_exists(con, "silver_call_transcripts"):
        total, texts, intents = con.execute("""
            SELECT count(*), count(DISTINCT customer_text), count(DISTINCT detected_intents)
            FROM silver_call_transcripts""").fetchone()
        out.append(
            f"- `call_transcripts`: {total:,} rows but only {texts} distinct `customer_text` "
            f"values and {intents} distinct `detected_intents`. The texts are templates and "
            "can't train an intent classifier, so intent labels are team-generated (step 1c)."
        )
    if table_exists(con, "gold_card_products"):
        numbers = [
            r[0]
            for r in con.execute(
                "SELECT product_number FROM gold_card_products "
                "USING SAMPLE 10000 ROWS (reservoir, 42)"
            ).fetchall()
        ]
        ok = sum(luhn_ok(n) for n in numbers)
        out.append(
            f"- Only {_pct(ok, len(numbers))} of card numbers pass the Luhn check (sample of "
            f"{len(numbers):,}). The PII guard therefore tokenizes any 15–16 digit number."
        )
    if table_exists(con, "silver_products"):
        currencies = ", ".join(
            f"{cur}: {n:,}"
            for cur, n in con.execute(
                "SELECT currency, count(*) FROM silver_products GROUP BY 1 ORDER BY 2 DESC"
            ).fetchall()
        )
        out.append(
            f"- Product currencies: {currencies}. There is no MXN even though Mexico has the "
            "most customers, so amounts must not be assumed to be in local currency."
        )
    if table_exists(con, "bronze_transactions"):
        variants = _one(
            con,
            """SELECT count(*) FROM bronze_transactions
                                WHERE trim(transaction_country) = 'Mexico'""",
        )
        out.append(
            f"- {variants:,} transactions spell the country `Mexico` instead of `México`; "
            "they are normalized in silver."
        )
    for name in ("customers", "products"):
        if table_exists(con, f"silver_{name}"):
            future, total = con.execute(f"""
                SELECT count(*) FILTER (WHERE last_updated > TIMESTAMP '{DATASET_END} 23:59:59'),
                       count(*)
                FROM silver_{name}""").fetchone()
            out.append(
                f"- {name}: {future:,} rows ({_pct(future, total)}) have `last_updated` after the "
                f"dataset end ({DATASET_END}), i.e. in the future. They are kept (the snapshot "
                "is still the latest version) but should not be trusted for recency logic."
            )
    return out


def write_report(con, dest: Path, run_id: str) -> None:
    drift = con.execute(
        "SELECT table_name, column_name, kind, count(*) FROM etl_schema_drift GROUP BY ALL"
    ).fetchall()
    sections = [
        "# Data quality report",
        "",
        f"Generated by `pipelines/etl/run.py` · run `{run_id}` · "
        f"{datetime.now(UTC):%Y-%m-%d %H:%M} UTC. Dataset: LATAM Bank v1.0.0 (synthetic).",
        "",
        "**Freshness policy:** batch runs are incremental by file. Every new or changed source "
        "file (including late partitions) is loaded on the next run, and keys are upserted, "
        "so re-runs never duplicate rows. The source is a static snapshot ending 2026-06-17, "
        "so the latest dates below are the dataset's end, not a live lag.",
        "",
        "## Tables",
        *table_summary(con),
        "",
        "## Key findings",
        *findings(con),
        "",
        "## Contract violations (quarantined rows)",
        *quarantine_reasons(con),
        "",
        "## Late or misaligned arrivals",
        *late_arrivals(con),
        "",
        "## Schema drift",
        *([f"- {t}.{c}: {k} ({n} runs)" for t, c, k, n in drift] or ["- None detected."]),
        "",
        "## Null rates (silver, columns with any nulls)",
        *null_rates(con),
        "",
    ]
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(sections), encoding="utf-8")
