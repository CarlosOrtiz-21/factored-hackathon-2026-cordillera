"""Encrypted SQLite vault: same contract as the in-memory one, plus persistence and
no personal data in clear on disk."""

import pytest
from cryptography.fernet import Fernet, InvalidToken

from cardagent.pii.sqlite_vault import SqliteTokenVault, load_or_create_key
from cardagent.pii.vault import UnknownTokenError

CARD = "4028 6208 5692 4060"


@pytest.fixture
def key():
    return Fernet.generate_key()


def test_same_contract_as_memory_vault(tmp_path, key):
    v = SqliteTokenVault(tmp_path / "v.db", key)
    t1 = v.tokenize("t1", "TARJETA", CARD)
    assert t1 == "<TARJETA_1>"
    assert v.tokenize("t1", "TARJETA", "4028-6208-5692-4060") == t1  # normalized match
    assert v.tokenize("t1", "TARJETA", "5500 0000 0000 0004") == "<TARJETA_2>"
    assert v.resolve("t1", t1) == CARD
    with pytest.raises(UnknownTokenError):
        v.resolve("t2", t1)  # tokens are scoped to their thread
    v.clear("t1")
    assert v.tokens("t1") == []


def test_survives_a_restart(tmp_path, key):
    SqliteTokenVault(tmp_path / "v.db", key).tokenize("t1", "TARJETA", CARD)
    reopened = SqliteTokenVault(tmp_path / "v.db", key)  # e.g. after a server restart
    assert reopened.resolve("t1", "<TARJETA_1>") == CARD


def test_nothing_personal_is_stored_in_clear(tmp_path, key):
    v = SqliteTokenVault(tmp_path / "v.db", key)
    v.tokenize("t1", "TARJETA", CARD)
    v.tokenize("t1", "EMAIL", "ana@correo.com")
    v.close()
    raw = (tmp_path / "v.db").read_bytes()
    for secret in (b"4028", b"4060", b"ana@correo.com", b"40286208"):
        assert secret not in raw


def test_wrong_key_cannot_read_values(tmp_path, key):
    SqliteTokenVault(tmp_path / "v.db", key).tokenize("t1", "TARJETA", CARD)
    other = SqliteTokenVault(tmp_path / "v.db", Fernet.generate_key())
    with pytest.raises(InvalidToken):
        other.resolve("t1", "<TARJETA_1>")


def test_key_file_is_created_once(tmp_path):
    path = tmp_path / "keys" / "vault.key"
    assert load_or_create_key(path) == load_or_create_key(path)
