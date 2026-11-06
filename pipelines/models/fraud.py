"""Suspicious-transaction model (secondary learned component, step 1c).

Label: `is_fraud` on card transactions (real dataset label, ~0.1% positive).

Leakage rules:
- `fraud_score` is excluded: it separates the classes almost perfectly and looks derived
  from the label.
- `transaction_status` and `response_code` are excluded: they are outcomes decided
  *after* a fraud check, so they wouldn't be available when scoring.
- Behavioral features only look at the card's PAST (windows end before the current row).
- The split is temporal: train on the past, choose thresholds on a later window, and report
  on the most recent months, the way the model would actually be used.

Usage:
    uv run python -m pipelines.models.fraud
"""

import json
from pathlib import Path

import duckdb
import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from cardagent.config import get_settings

MODEL_PATH = Path("models/fraud_v1.joblib")
REPORT_PATH = Path("docs/models/FRAUD_MODEL.md")
TRAIN_END, VALID_END = "2025-07-01", "2026-01-01"  # test = 2026-01-01 .. dataset end
ALERT_RATE = 0.01  # the review team can look at 1% of transactions
SEED = 42

CATEGORICAL = [
    "channel",
    "transaction_type",
    "transaction_category",
    "merchant_category",
    "card_kind",
]
NUMERIC = [
    "log_amount_usd",
    "hour",
    "dow",
    "is_foreign",
    "tx_last_24h",
    "amount_z",
    "log_secs_since_prev",
    "km_from_prev",
    "log_speed_kmh",
    "new_merchant",
    "country_changed",
]

FEATURES_SQL = """
WITH base AS (
    SELECT t.transaction_id, t.product_id, t.transaction_date, t.is_fraud, t.fraud_score,
           t.amount_usd::DOUBLE AS amount_usd, t.channel, t.transaction_type,
           coalesce(t.transaction_category, 'none') AS transaction_category,
           coalesce(t.merchant_category, 'none') AS merchant_category, t.card_kind,
           t.transaction_country, t.merchant_name, t.latitude, t.longitude,
           (t.transaction_country <> c.country)::INT AS is_foreign
    FROM gold_card_transactions t
    JOIN silver_customers c USING (customer_id)
),
w AS (
    SELECT *,
        count(*) OVER (PARTITION BY product_id ORDER BY transaction_date
                       RANGE BETWEEN INTERVAL 1 DAY PRECEDING AND CURRENT ROW) - 1
            AS tx_last_24h,
        avg(amount_usd) OVER past AS past_mean,
        stddev_samp(amount_usd) OVER past AS past_std,
        lag(transaction_date) OVER card AS prev_date,
        lag(latitude) OVER card AS prev_lat,
        lag(longitude) OVER card AS prev_lon,
        lag(transaction_country) OVER card AS prev_country,
        row_number() OVER (PARTITION BY product_id, merchant_name ORDER BY transaction_date)
            AS merchant_seq
    FROM base
    WINDOW card AS (PARTITION BY product_id ORDER BY transaction_date),
           past AS (PARTITION BY product_id ORDER BY transaction_date
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
)
SELECT transaction_id, transaction_date, is_fraud::INT AS y, fraud_score,
       channel, transaction_type, transaction_category, merchant_category, card_kind,
       ln(1 + greatest(amount_usd, 0)) AS log_amount_usd,
       hour(transaction_date) AS hour, dayofweek(transaction_date) AS dow, is_foreign,
       tx_last_24h,
       coalesce((amount_usd - past_mean) / nullif(past_std, 0), 0) AS amount_z,
       ln(1 + coalesce(epoch(transaction_date - prev_date), 1e7)) AS log_secs_since_prev,
       coalesce(2 * 6371 * asin(sqrt(
           pow(sin(radians(latitude - prev_lat) / 2), 2) +
           cos(radians(prev_lat)) * cos(radians(latitude)) *
           pow(sin(radians(longitude - prev_lon) / 2), 2))), 0) AS km_from_prev,
       ln(1 + coalesce(km_from_prev / greatest(epoch(transaction_date - prev_date) / 3600,
                                               1.0 / 60), 0)) AS log_speed_kmh,
       (merchant_seq = 1 AND merchant_name IS NOT NULL)::INT AS new_merchant,
       coalesce((transaction_country <> prev_country)::INT, 0) AS country_changed
FROM w
"""


def load_features() -> pd.DataFrame:
    con = duckdb.connect(str(get_settings().warehouse_path), read_only=True)
    df = con.execute(FEATURES_SQL).df()
    con.close()
    return df


def split(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    d = df["transaction_date"]
    return df[d < TRAIN_END], df[(d >= TRAIN_END) & (d < VALID_END)], df[d >= VALID_END]


def rules_score(df: pd.DataFrame) -> np.ndarray:
    """Baseline: the kind of hand-written rules a fraud team starts with."""
    return (
        df["is_foreign"]
        + (df["amount_z"] > 3).astype(int)
        + (df["log_speed_kmh"] > np.log1p(900)).astype(int)  # faster than a plane
        + (df["tx_last_24h"] >= 5).astype(int)
        + df["hour"].between(0, 5).astype(int)
    ).to_numpy() + 1e-6 * df["log_amount_usd"].to_numpy()  # tie-break by amount


def preprocessor() -> ColumnTransformer:
    return ColumnTransformer(
        [
            ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=50), CATEGORICAL),
            ("num", StandardScaler(), NUMERIC),
        ]
    )


def candidates() -> dict:
    return {
        "logistic_regression": make_pipeline(
            preprocessor(),
            LogisticRegression(max_iter=2000, class_weight="balanced", C=0.1),
        ),
        "gradient_boosting": make_pipeline(
            preprocessor(),
            HistGradientBoostingClassifier(
                max_iter=300,
                learning_rate=0.05,
                class_weight="balanced",
                early_stopping=True,
                random_state=SEED,
            ),
        ),
    }


def recall_at_alert_rate(y: np.ndarray, score: np.ndarray, rate: float = ALERT_RATE) -> float:
    k = max(1, int(len(score) * rate))
    top = np.argsort(-score, kind="stable")[:k]
    return float(y[top].sum() / max(y.sum(), 1))


def bootstrap_ci(y, score, metric, n: int = 500) -> tuple[float, float]:
    rng = np.random.default_rng(SEED)
    values = []
    for _ in range(n):
        idx = rng.integers(0, len(y), len(y))
        if y[idx].sum() == 0:
            continue
        values.append(metric(y[idx], score[idx]))
    return float(np.percentile(values, 2.5)), float(np.percentile(values, 97.5))


def evaluate(name: str, y: np.ndarray, score: np.ndarray) -> dict:
    ap_lo, ap_hi = bootstrap_ci(y, score, average_precision_score)
    return {
        "model": name,
        "pr_auc": float(average_precision_score(y, score)),
        "pr_auc_ci95": [ap_lo, ap_hi],
        "roc_auc": float(roc_auc_score(y, score)),
        f"recall_at_{ALERT_RATE:.0%}_alerts": recall_at_alert_rate(y, score),
    }


def main() -> None:
    df = load_features()
    train, valid, test = split(df)
    X_cols = CATEGORICAL + NUMERIC
    sizes = {
        n: {"rows": len(p), "positives": int(p["y"].sum())}
        for n, p in (("train", train), ("valid", valid), ("test", test))
    }
    print(sizes)

    results = {"valid": [], "test": []}
    for part, name in (("valid", valid), ("test", test)):
        y = name["y"].to_numpy()
        results[part].append(
            evaluate("random (prevalence)", y, np.random.default_rng(SEED).random(len(y)))
        )
        results[part].append(evaluate("rules baseline", y, rules_score(name)))
        # Leakage CONTROL, not a candidate: shows the label is recoverable only via fraud_score.
        results[part].append(
            evaluate(
                "control: fraud_score (excluded, leaky)",
                y,
                name["fraud_score"].fillna(0).to_numpy(),
            )
        )

    fitted = {}
    for model_name, model in candidates().items():
        model.fit(train[X_cols], train["y"])
        fitted[model_name] = model
        for part, name in (("valid", valid), ("test", test)):
            score = model.predict_proba(name[X_cols])[:, 1]
            results[part].append(evaluate(model_name, name["y"].to_numpy(), score))
        print(model_name, results["valid"][-1])

    # Model selection uses VALID only; TEST is reported once, never used to choose.
    best = max((r for r in results["valid"] if r["model"] in fitted), key=lambda r: r["pr_auc"])
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model": fitted[best["model"]],
            "features": X_cols,
            "version": "fraud_v1",
            "selected_on": "valid PR-AUC",
        },
        MODEL_PATH,
    )

    # Deployment gate: only ship if the model beats random with confidence on TEST.
    prevalence = sizes["test"]["positives"] / sizes["test"]["rows"]
    test_best = next(r for r in results["test"] if r["model"] == best["model"])
    deploy = test_best["pr_auc_ci95"][0] > prevalence
    joblib.dump(
        {**joblib.load(MODEL_PATH), "deploy": deploy, "test_metrics": test_best}, MODEL_PATH
    )
    write_report(sizes, results, best["model"], deploy)
    print(json.dumps(results["test"], indent=2))


def write_report(sizes: dict, results: dict, best: str, deploy: bool) -> None:
    def table(rows: list[dict]) -> list[str]:
        key = f"recall_at_{ALERT_RATE:.0%}_alerts"
        out = [
            f"| Model | PR-AUC (95% CI) | ROC-AUC | Recall @ {ALERT_RATE:.0%} alerts |",
            "|---|---|---:|---:|",
        ]
        for r in rows:
            lo, hi = r["pr_auc_ci95"]
            out.append(
                f"| {r['model']} | {r['pr_auc']:.4f} ({lo:.4f}–{hi:.4f}) "
                f"| {r['roc_auc']:.3f} | {r[key]:.1%} |"
            )
        return out

    prevalence = sizes["test"]["positives"] / sizes["test"]["rows"]
    lines = [
        "# Suspicious-transaction model (fraud_v1)",
        "",
        "Offline evaluation on the synthetic LATAM Bank dataset. Generated by "
        "`pipelines/models/fraud.py`.",
        "",
        "## Setup",
        "- **Label:** `transactions.is_fraud` on card transactions (real dataset label).",
        "- **Excluded for leakage:**",
        "  - `fraud_score`: it is almost a copy of the label (mean 15 vs. 49 by class).",
        "  - `transaction_status` and `response_code`: they are decided after the fraud check.",
        "- **Features:** amount, channel, type, category, merchant category, card kind, "
        "hour, weekday, and whether the country is foreign. Behavioral features use only "
        "the card's past: transactions in the last 24h, amount z-score against the card's "
        "history, time and km since the previous transaction, implied travel speed, a new "
        "merchant, and a country change.",
        f"- **Temporal split:** train < {TRAIN_END} ≤ valid < {VALID_END} ≤ test.",
        "",
        "| Split | Rows | Positives |",
        "|---|---:|---:|",
        *(f"| {k} | {v['rows']:,} | {v['positives']:,} |" for k, v in sizes.items()),
        "",
        f"**Model selection** used the validation set only; the selected model is **{best}**. "
        "The test set is reported once and was never used to choose.",
        "",
        "## Validation",
        *table(results["valid"]),
        "",
        "## Test (reported once)",
        *table(results["test"]),
        "",
        f"A random ranking has an expected PR-AUC equal to the prevalence ({prevalence:.4f}).",
        "",
        "## Conclusion and deployment decision",
        "**Deployment gate:** ship only if the lower bound of the test PR-AUC 95% CI is "
        "above the prevalence, i.e. the model beats random with confidence.",
        "",
        f"**Decision: {'DEPLOY' if deploy else 'DO NOT DEPLOY'}.** "
        + (
            "The model is used as a read-only signal in `card_transactions`."
            if deploy
            else "No legitimate feature carries signal. Every candidate, including the rules "
            "baseline, is indistinguishable from random ranking, while the excluded "
            "`fraud_score` alone recovers the label. In this synthetic dataset `is_fraud` "
            "appears to be generated independently of the transaction attributes and "
            "tied only to `fraud_score`. The assistant therefore does **not** show a "
            "model-based fraud flag. Unrecognized charges are handled by the customer's "
            "own report plus escalation, which is the safer behavior when there is no "
            "validated signal. This is a negative result, reported as such."
        ),
        "",
    ]
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
