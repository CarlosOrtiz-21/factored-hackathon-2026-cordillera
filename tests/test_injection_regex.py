"""The injection regex (backup layer), measured on its own DEV set, never on the golden set.

datasets/injection_dev_v1.csv has attack families (ES/PT/EN) plus tricky benign messages
("olvidé las instrucciones", "compras sin autorización", "pagos sin contacto"). The intent
classifier's 505 training utterances are all benign and must never trigger it.
"""

import csv

from cardagent.agents.guards import detect_injection


def rows(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_regex_catches_attack_families_on_the_dev_set():
    attacks = [r for r in rows("datasets/injection_dev_v1.csv") if r["label"] == "1"]
    missed = [r["text"] for r in attacks if not detect_injection(r["text"])]
    assert len(missed) / len(attacks) <= 0.1, missed
    assert {r["family"] for r in attacks} == {
        r["family"] for r in attacks if detect_injection(r["text"])
    }  # every family is covered at least once


def test_regex_does_not_flag_benign_lookalikes():
    benign = [r["text"] for r in rows("datasets/injection_dev_v1.csv") if r["label"] == "0"]
    assert [t for t in benign if detect_injection(t)] == []


def test_regex_does_not_flag_real_card_support_utterances():
    training = rows("datasets/intents/card_intents_v1.csv")
    assert [r["text"] for r in training if detect_injection(r["text"])] == []
