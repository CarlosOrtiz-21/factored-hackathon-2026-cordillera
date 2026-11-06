"""Export: a small, deterministic SQLite database for the mock bank API (step 2).

The API does not need 150k customers: a fixed sample of demo customers who hold at
least one card is enough, and keeps the app fast. The sample is chosen by an md5 hash
of the customer id (not random), so every run picks exactly the same customers.
"""

import sqlite3
from contextlib import closing
from pathlib import Path

DEMO_CUSTOMERS = 2_000
SAMPLE_SALT = "cardagent-demo-v1"

TABLES = {
    "customers": """
        SELECT customer_id, first_name, last_name, document_type, document_number, email,
               mobile_phone, country, segment, customer_status
        FROM silver_customers
        WHERE customer_id IN (SELECT customer_id FROM demo_ids)
    """,
    "cards": """
        SELECT product_id, customer_id, card_kind, product_type, product_number, last4,
               currency, product_status, credit_limit::DOUBLE AS credit_limit,
               current_balance::DOUBLE AS current_balance,
               expiration_date::VARCHAR AS expiration_date,
               last_transaction_date::VARCHAR AS last_transaction_date
        FROM gold_card_products
        WHERE customer_id IN (SELECT customer_id FROM demo_ids)
    """,
    "card_transactions": """
        SELECT transaction_id, product_id, customer_id,
               transaction_date::VARCHAR AS transaction_date, transaction_type,
               transaction_category, amount::DOUBLE AS amount, currency,
               amount_usd::DOUBLE AS amount_usd, channel, merchant_name,
               transaction_country, transaction_city, transaction_status
        FROM gold_card_transactions
        WHERE customer_id IN (SELECT customer_id FROM demo_ids)
    """,
}

DDL = """
CREATE TABLE customers (
    customer_id TEXT PRIMARY KEY, first_name TEXT, last_name TEXT, document_type TEXT,
    document_number TEXT, email TEXT, mobile_phone TEXT, country TEXT, segment TEXT,
    customer_status TEXT
);
CREATE TABLE cards (
    product_id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(customer_id),
    card_kind TEXT, product_type TEXT, product_number TEXT, last4 TEXT, currency TEXT,
    product_status TEXT, credit_limit REAL, current_balance REAL, expiration_date TEXT,
    last_transaction_date TEXT
);
CREATE TABLE card_transactions (
    transaction_id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES cards(product_id),
    customer_id TEXT NOT NULL, transaction_date TEXT, transaction_type TEXT,
    transaction_category TEXT, amount REAL, currency TEXT, amount_usd REAL, channel TEXT,
    merchant_name TEXT, transaction_country TEXT, transaction_city TEXT,
    transaction_status TEXT
);
CREATE INDEX idx_cards_customer ON cards(customer_id);
CREATE INDEX idx_tx_product_date ON card_transactions(product_id, transaction_date);
"""


def export_bank_db(con, dest: Path, n_customers: int = DEMO_CUSTOMERS) -> dict[str, int]:
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE demo_ids AS
        SELECT DISTINCT c.customer_id
        FROM silver_customers c
        JOIN gold_card_products p USING (customer_id)
        WHERE c.customer_status = 'Active' AND NOT c._is_orphan
        ORDER BY md5(c.customer_id || '{SAMPLE_SALT}')
        LIMIT {int(n_customers)}
    """)

    # Build into a temp file and swap at the end: readers never see a half-built db.
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".db.tmp")
    tmp.unlink(missing_ok=True)
    counts: dict[str, int] = {}
    # contextlib.closing: sqlite3's own context manager commits but does NOT close, and
    # Windows refuses to rename a file that is still open.
    with closing(sqlite3.connect(tmp)) as db, db:
        db.executescript(DDL)
        for table, query in TABLES.items():
            result = con.execute(query)
            cols = [d[0] for d in result.description]
            rows = result.fetchall()
            placeholders = ", ".join("?" for _ in cols)
            db.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})", rows)
            counts[table] = len(rows)
    tmp.replace(dest)
    return counts
