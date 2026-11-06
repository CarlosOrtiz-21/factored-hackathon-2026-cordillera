"""Step 1c tests: dataset integrity, leakage controls, and metric helpers.

They avoid downloading the e5 model or reading the warehouse, so they stay fast.
"""

import numpy as np
import pandas as pd

from cardagent.schemas.enums import Intent
from pipelines.models import fraud, intents


def test_intent_dataset_integrity():
    df = intents.load()
    assert set(df["intent"]) == {i.value for i in Intent}  # labels match the runtime enum
    assert set(df["lang"]) == {"es", "pt"}
    assert not df["text"].str.lower().duplicated().any()
    # every paraphrase group is exactly one intent and has both languages
    per_group = df.groupby("group").agg(intents=("intent", "nunique"), langs=("lang", "nunique"))
    assert (per_group["intents"] == 1).all()
    assert (per_group["langs"] == 2).all()
    # balanced classes
    assert df["intent"].value_counts().nunique() == 1


def test_intent_split_has_no_group_leakage_and_covers_all_intents():
    train, test = intents.split_by_group(intents.load())
    assert not set(train["group"]) & set(test["group"])
    assert set(test["intent"]) == set(train["intent"]) == {i.value for i in Intent}
    # deterministic: same seed, same split
    _, test_again = intents.split_by_group(intents.load())
    assert test["id"].tolist() == test_again["id"].tolist()


def test_keyword_rules_prefer_procedure_questions_over_actions():
    preds = intents.keyword_predict(
        [
            "¿Qué hago si me roban la tarjeta?",
            "Me robaron la tarjeta",
            "Quiero hablar con un asesor",
        ]
    )
    assert preds.tolist() == ["card_policy", "card_block", "human_request"]


def test_threshold_reaches_target_precision():
    labels = ["a", "b"]
    proba = np.array([[0.9, 0.1], [0.8, 0.2], [0.55, 0.45], [0.6, 0.4]])
    y_true = np.array(["a", "a", "b", "a"])  # the 0.55 prediction is wrong
    t = intents.threshold_for_precision(proba, y_true, labels, target=1.0)
    assert 0.55 < t <= 0.6


def test_fraud_features_exclude_leaky_columns():
    features = set(fraud.CATEGORICAL + fraud.NUMERIC)
    assert not features & {"fraud_score", "transaction_status", "response_code", "y"}


def test_recall_at_alert_rate():
    y = np.array([1, 0, 0, 0, 1, 0, 0, 0, 0, 0])
    score = np.array([0.9, 0.8, 0.1, 0.1, 0.2, 0.1, 0.1, 0.1, 0.1, 0.1])
    assert fraud.recall_at_alert_rate(y, score, rate=0.1) == 0.5  # top-1 catches 1 of 2


def test_fraud_temporal_split_is_ordered():
    dates = pd.to_datetime(["2024-01-01", "2025-08-01", "2026-02-01"])
    df = pd.DataFrame({"transaction_date": dates, "y": [0, 0, 1]})
    train, valid, test = fraud.split(df)
    assert train["transaction_date"].max() < valid["transaction_date"].min()
    assert valid["transaction_date"].max() < test["transaction_date"].min()
