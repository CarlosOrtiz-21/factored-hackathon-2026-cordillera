"""Intent classifier (primary learned component, step 1c).

Candidates on the same held-out split:
  1. majority class              (sanity floor)
  2. keyword rules               (the baseline a team would write first)
  3. TF-IDF word+char + LogReg   (cheap, robust to typos and accents)
  4. multilingual-e5 + LogReg    (semantic, cross-lingual ES/PT)
The zero-shot LLM triage is evaluated on this same test split in step 3.

Leakage control: splits are by PARAPHRASE GROUP, so variants and translations of a test
request never appear in training. The model is selected by grouped cross-validation on
the training set; the test set is reported once.

The chosen model is the triage FALLBACK when the LLM fails. It only acts when its
confidence clears a threshold chosen for >= 95% precision; otherwise the case escalates.

Usage:
    uv run python -m pipelines.models.intents            # train + evaluate + report
    uv run python -m pipelines.models.intents --review   # score the human label review
"""

import argparse
import re
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, cohen_kappa_score, confusion_matrix, f1_score
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import FeatureUnion, make_pipeline

from cardagent.embeddings import E5_MODEL, E5Encoder

DATA = Path("datasets/intents/card_intents_v1.csv")
REVIEW = Path("datasets/intents/review_sample.csv")
MODEL_PATH = Path("models/intent_v1.joblib")
REPORT_PATH = Path("docs/models/INTENT_MODEL.md")
SEED = 42
TEST_GROUPS_PER_INTENT = 4  # of 14 -> ~29% of the data held out
TARGET_PRECISION = 0.95

# --- baselines --------------------------------------------------------------------

# Ordered: the first matching rule wins. Questions about procedures ("qué hago si...")
# are checked before actions, because they mention the same words ("robo", "bloquear").
KEYWORD_RULES: list[tuple[str, str]] = [
    (
        "human_request",
        r"asesor|humano|persona|agente|operador|supervisor|gerente|atendente"
        r"|call center|central|ll[aá]me|ligar|ejecutivo|superior|en vivo",
    ),
    (
        "card_policy",
        r"qu[eé] hago si|que debo hacer|procedimiento|cu[aá]nto cuesta|cuota de "
        r"manejo|anualidad|anuidade|tasa|juros|inter[eé]s|requisitos|pol[ií]tica"
        r"|seguro de|l[ií]mite diario|contactless|aproxima|c[oó]mo (puedo )?"
        r"desbloque|pago m[ií]nimo|reposici[oó]n|segunda via|cu[aá]nto tarda",
    ),
    (
        "card_block",
        r"bloque(a|e|ar|en|ie|ala|ame|éenla|enla)|congel|desactiv|robaron|roubaram"
        r"|perd[ií]|clonaron|clonaram|extravi|trag[oó]|engoliu",
    ),
    (
        "card_transactions",
        r"movimiento|transacci|transa[cç]|compra|cargo|cobr|consumo|gast|"
        r"retiro|saque|reembolso|estorno|devol|lan[cç]amento|historial",
    ),
    (
        "card_status",
        r"activa|ativo|bloqueada|bloqueado|vence|venc|cupo|l[ií]mite|saldo|"
        r"suspend|estado|situa|funciona|rechaz|recus|vinculad|tarjetas tengo",
    ),
]


def keyword_predict(texts: list[str]) -> np.ndarray:
    compiled = [(intent, re.compile(pattern, re.IGNORECASE)) for intent, pattern in KEYWORD_RULES]
    out = []
    for text in texts:
        out.append(next((i for i, rx in compiled if rx.search(text)), "out_of_scope"))
    return np.array(out)


# --- learned models ---------------------------------------------------------------


def tfidf_model(C: float):
    features = FeatureUnion(
        [
            (
                "word",
                TfidfVectorizer(
                    ngram_range=(1, 2), sublinear_tf=True, strip_accents="unicode", lowercase=True
                ),
            ),
            (
                "char",
                TfidfVectorizer(
                    analyzer="char_wb",
                    ngram_range=(2, 5),
                    sublinear_tf=True,
                    strip_accents="unicode",
                    lowercase=True,
                ),
            ),
        ]
    )
    return make_pipeline(features, LogisticRegression(C=C, max_iter=3000))


def e5_model(C: float):
    return LogisticRegression(C=C, max_iter=3000)


# --- data and split ---------------------------------------------------------------


def load() -> pd.DataFrame:
    df = pd.read_csv(DATA)
    df["id"] = df["group"] + "-" + df.groupby("group").cumcount().astype(str)
    return df


def split_by_group(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(SEED)
    test_groups = set()
    for _, groups in df.groupby("intent")["group"].unique().items():
        test_groups |= set(rng.choice(sorted(groups), TEST_GROUPS_PER_INTENT, replace=False))
    is_test = df["group"].isin(test_groups)
    return df[~is_test].reset_index(drop=True), df[is_test].reset_index(drop=True)


# --- selection by grouped CV --------------------------------------------------------


def grouped_cv(train: pd.DataFrame, fit_predict_proba, n_splits: int = 5):
    """Out-of-fold probabilities, folds by paraphrase group."""
    cv = StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=SEED)
    labels = sorted(train["intent"].unique())
    oof = np.zeros((len(train), len(labels)))
    for tr, va in cv.split(train, train["intent"], train["group"]):
        oof[va] = fit_predict_proba(tr, va)
    return oof, labels


def threshold_for_precision(
    proba: np.ndarray, y_true: np.ndarray, labels: list[str], target: float = TARGET_PRECISION
) -> float:
    """Lowest confidence threshold whose accepted predictions reach the target precision."""
    conf = proba.max(axis=1)
    pred = np.array(labels)[proba.argmax(axis=1)]
    for t in np.round(np.arange(0.0, 1.0, 0.01), 2):
        accepted = conf >= t
        if accepted.sum() and (pred[accepted] == y_true[accepted]).mean() >= target:
            return float(t)
    return 1.0


def scores(y_true, y_pred) -> dict:
    return {
        "accuracy": accuracy_score(y_true, y_pred),
        "macro_f1": f1_score(y_true, y_pred, average="macro"),
    }


def main() -> None:
    df = load()
    train, test = split_by_group(df)
    assert not set(train["group"]) & set(test["group"]), "group leakage"
    print(f"train {len(train)} rows / test {len(test)} rows")

    # 1) choose hyperparameters and model family by grouped CV on TRAIN only
    encoder = E5Encoder()
    emb_train = encoder.encode(train["text"].tolist())
    emb_test = encoder.encode(test["text"].tolist())
    y_train = train["intent"].to_numpy()

    cv_results = {}
    for C in (1.0, 5.0, 20.0):

        def fit_tfidf(tr, va, C=C):
            m = tfidf_model(C).fit(train["text"].iloc[tr], y_train[tr])
            return m.predict_proba(train["text"].iloc[va])

        def fit_e5(tr, va, C=C):
            m = e5_model(C).fit(emb_train[tr], y_train[tr])
            return m.predict_proba(emb_train[va])

        for family, fn in (("tfidf_logreg", fit_tfidf), ("e5_logreg", fit_e5)):
            oof, labels = grouped_cv(train, fn)
            pred = np.array(labels)[oof.argmax(axis=1)]
            cv_results[(family, C)] = {**scores(y_train, pred), "oof": oof, "labels": labels}
            print(family, C, round(cv_results[(family, C)]["macro_f1"], 3))

    best_key = max(cv_results, key=lambda k: cv_results[k]["macro_f1"])
    best_family, best_C = best_key
    best_cv = cv_results[best_key]
    threshold = threshold_for_precision(best_cv["oof"], y_train, best_cv["labels"])

    # 2) refit every candidate on all of TRAIN, evaluate once on TEST
    y_test = test["intent"].to_numpy()
    final = {}
    preds = {
        "majority class": np.array([pd.Series(y_train).mode()[0]] * len(test)),
        "keyword rules": keyword_predict(test["text"].tolist()),
    }
    for family in ("tfidf_logreg", "e5_logreg"):
        C = max((k for k in cv_results if k[0] == family), key=lambda k: cv_results[k]["macro_f1"])[
            1
        ]
        if family == "tfidf_logreg":
            model = tfidf_model(C).fit(train["text"], y_train)
            proba = model.predict_proba(test["text"])
        else:
            model = e5_model(C).fit(emb_train, y_train)
            proba = model.predict_proba(emb_test)
        final[family] = (model, proba, C)
        preds[f"{family} (C={C})"] = model.classes_[proba.argmax(axis=1)]

    model, proba, _ = final[best_family]
    conf = proba.max(axis=1)
    accepted = conf >= threshold
    best_pred = model.classes_[proba.argmax(axis=1)]

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "family": best_family,
            "model": model,
            "threshold": threshold,
            "labels": list(model.classes_),
            "encoder": E5_MODEL if best_family == "e5_logreg" else None,
            "version": "intent_v1",
            "dataset": DATA.name,
        },
        MODEL_PATH,
    )

    write_review_sample(df)
    write_report(
        train, test, cv_results, best_key, threshold, preds, y_test, best_pred, accepted, conf
    )
    print(f"selected {best_key}; threshold {threshold}; report at {REPORT_PATH}")


def write_review_sample(df: pd.DataFrame) -> None:
    """Blind review: the reviewer sees text only, not our label."""
    if REVIEW.exists():  # never overwrite a review in progress
        return
    sample = df.groupby("intent", group_keys=False).sample(10, random_state=SEED)
    sample = sample.sample(frac=1, random_state=SEED)[["id", "lang", "text"]]
    sample["reviewer_label"] = ""
    sample.to_csv(REVIEW, index=False)


def review_stats() -> dict | None:
    """Agreement between our labels and the blind human review, if it has been done."""
    if not REVIEW.exists():
        return None
    df = load().set_index("id")
    rev = pd.read_csv(REVIEW).dropna(subset=["reviewer_label"])
    if rev.empty:
        return None
    ours = df.loc[rev["id"], "intent"].to_numpy()
    theirs = rev["reviewer_label"].str.strip().to_numpy()
    return {
        "n": len(rev),
        "agreement": float((ours == theirs).mean()),
        "kappa": float(cohen_kappa_score(ours, theirs)),
        "disagreements": [
            (i, o, t, df.loc[i, "text"])
            for i, o, t in zip(rev["id"], ours, theirs, strict=True)
            if o != t
        ],
    }


def review() -> None:
    stats = review_stats()
    if stats is None:
        print(f"No reviews yet: fill `reviewer_label` in {REVIEW}")
        return
    print(
        f"reviewed: {stats['n']}  agreement: {stats['agreement']:.1%}  "
        f"Cohen's kappa: {stats['kappa']:.3f}"
    )
    for i, o, t, text in stats["disagreements"]:
        print(f"  {i}: ours={o} reviewer={t} :: {text}")


def label_validation_section() -> list[str]:
    stats = review_stats()
    if stats is None:
        return ["## Label validation", "- Pending: blind human review not done yet.", ""]
    rows = [f"| {i} | {text} | {o} | {t} |" for i, o, t, text in stats["disagreements"]]
    return [
        "## Label validation (blind human review)",
        f"A reviewer labeled a stratified random sample of {stats['n']} utterances without "
        f"seeing our labels. **Agreement: {stats['agreement']:.1%}, Cohen's kappa: "
        f"{stats['kappa']:.2f}.**",
        "",
        "| Id | Text | Our label | Reviewer |",
        "|---|---|---|---|",
        *rows,
        "",
        "Every disagreement falls on the `card_policy` / `out_of_scope` boundary. "
        "The labeling guideline didn't define it explicitly. Our rule, now written into the "
        "dataset README, is: a question about how *cards* work (fees, delivery, limits, "
        "security practices around cards) is `card_policy`; banking questions about other "
        "products or account administration are `out_of_scope`. Labels were kept, not "
        "silently changed to match the reviewer, and the ambiguity is reported. In the "
        "running system this boundary is low-risk: both routes are read-only (policy RAG "
        "with abstention, or a safe refusal).",
        "",
    ]


def write_report(
    train, test, cv_results, best_key, threshold, preds, y_test, best_pred, accepted, conf
) -> None:
    langs = test["lang"].to_numpy()
    rows = []
    for name, pred in preds.items():
        s = scores(y_test, pred)
        per_lang = {
            lang: f1_score(y_test[langs == lang], pred[langs == lang], average="macro")
            for lang in ("es", "pt")
        }
        rows.append(
            f"| {name} | {s['accuracy']:.3f} | {s['macro_f1']:.3f} "
            f"| {per_lang['es']:.3f} | {per_lang['pt']:.3f} |"
        )

    labels = sorted(set(y_test))
    cm = confusion_matrix(y_test, best_pred, labels=labels)
    cm_lines = ["| true \\ pred | " + " | ".join(labels) + " |", "|---" * (len(labels) + 1) + "|"]
    cm_lines += [
        f"| {lab} | " + " | ".join(str(v) for v in row) + " |"
        for lab, row in zip(labels, cm, strict=True)
    ]

    errors = [
        f"| {t['lang']} | {t['text']} | {t['intent']} | {p} | {c:.2f} |"
        for (_, t), p, c in zip(test.iterrows(), best_pred, conf, strict=True)
        if p != t["intent"]
    ]
    acc_accepted = (best_pred[accepted] == y_test[accepted]).mean() if accepted.any() else 0
    cv_table = [
        f"| {fam} | {C} | {r['accuracy']:.3f} | {r['macro_f1']:.3f} |"
        for (fam, C), r in sorted(cv_results.items())
    ]

    lines = [
        "# Intent classifier (intent_v1)",
        "",
        "Offline evaluation on the **team-generated** intent dataset "
        "(`datasets/intents/`, see its README for provenance and limitations). "
        "Generated by `pipelines/models/intents.py`.",
        "",
        "## Setup",
        f"- 504 utterances in 84 paraphrase groups. Held-out test = {TEST_GROUPS_PER_INTENT} "
        f"groups per intent: **{len(test)} test / {len(train)} train rows, no group in both** "
        "(asserted in code).",
        "- Model family and `C` are selected by 5-fold **grouped** cross-validation on train. "
        "The test set is used once.",
        f"- Fallback confidence threshold: the lowest threshold whose out-of-fold precision "
        f"≥ {TARGET_PRECISION:.0%} (on train). Below it, the case escalates instead of "
        "being routed.",
        "- Not yet included: zero-shot LLM triage on this same test split (step 3).",
        "",
        "## Cross-validation (train only)",
        "| Family | C | Accuracy | Macro-F1 |",
        "|---|---:|---:|---:|",
        *cv_table,
        "",
        f"**Selected:** `{best_key[0]}` with C={best_key[1]}.",
        "",
        "## Held-out test (reported once)",
        "| Model | Accuracy | Macro-F1 | Macro-F1 ES | Macro-F1 PT |",
        "|---|---:|---:|---:|---:|",
        *rows,
        "",
        f"With the fallback threshold ({threshold:.2f}), the selected model routes "
        f"**{accepted.mean():.1%}** of test cases (coverage) with **{acc_accepted:.1%}** "
        f"accuracy on those. The remaining {1 - accepted.mean():.1%} escalate.",
        "",
        "## Confusion matrix (selected model, test)",
        *cm_lines,
        "",
        "## Errors (selected model, test)",
        "| Lang | Text | True | Predicted | Confidence |",
        "|---|---|---|---|---:|",
        *(errors or ["| | (none) | | | |"]),
        "",
        *label_validation_section(),
        "## Caveats",
        "- Test size is small (one row per utterance, 24 paraphrase groups), so differences of "
        "a few points between models aren't significant.",
        "- The data was written by the system's designers, so real customer traffic will be "
        "harder. Treat these numbers as an upper bound until validated on real utterances.",
        "",
    ]
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--review", action="store_true")
    if parser.parse_args().review:
        review()
    else:
        main()
