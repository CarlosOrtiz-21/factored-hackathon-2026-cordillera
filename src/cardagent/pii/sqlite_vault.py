"""Persistent, encrypted token vault (SQLite).

Why: a block can wait minutes for approval. If the server restarts meanwhile, an in-memory
vault forgets which card <TARJETA_1> was, and the approved action can't run. This vault
survives restarts without storing personal data in clear:

- `ciphertext`: the real value, encrypted with Fernet (AES-128-CBC + HMAC-SHA256).
- `lookup`: HMAC-SHA256(key, label|normalized value). It lets us find "does this card
  already have a token in this thread?" without decrypting anything, and it can't be
  reversed without the key.

The key comes from settings (`VAULT_KEY`) or, for local demos, a key file in data/ that is
created once and gitignored. Losing the key makes the stored values unrecoverable: by design.
"""

import hashlib
import hmac
import sqlite3
import threading
from pathlib import Path

from cryptography.fernet import Fernet

from cardagent.pii.vault import UnknownTokenError, normalize

SCHEMA = """
CREATE TABLE IF NOT EXISTS pii_vault (
    thread_id TEXT NOT NULL,
    token TEXT NOT NULL,
    label TEXT NOT NULL,
    lookup TEXT NOT NULL,
    ciphertext BLOB NOT NULL,
    PRIMARY KEY (thread_id, token),
    UNIQUE (thread_id, lookup)
);
"""


def load_or_create_key(path: Path) -> bytes:
    if path.exists():
        return path.read_bytes().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    path.write_bytes(key)
    return key


class SqliteTokenVault:
    def __init__(self, db_path: Path | str, key: bytes):
        self._fernet = Fernet(key)
        self._hmac_key = hashlib.sha256(b"vault-lookup|" + key).digest()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)

    def _lookup(self, label: str, value: str) -> str:
        msg = f"{label}|{normalize(label, value)}".encode()
        return hmac.new(self._hmac_key, msg, hashlib.sha256).hexdigest()

    def tokenize(self, thread_id: str, label: str, value: str) -> str:
        lookup = self._lookup(label, value)
        with self._lock:
            row = self._conn.execute(
                "SELECT token FROM pii_vault WHERE thread_id = ? AND lookup = ?",
                (thread_id, lookup),
            ).fetchone()
            if row:
                return row[0]
            count = self._conn.execute(
                "SELECT count(*) FROM pii_vault WHERE thread_id = ? AND label = ?",
                (thread_id, label),
            ).fetchone()[0]
            token = f"<{label}_{count + 1}>"
            self._conn.execute(
                "INSERT INTO pii_vault VALUES (?, ?, ?, ?, ?)",
                (thread_id, token, label, lookup, self._fernet.encrypt(value.encode())),
            )
            self._conn.commit()
            return token

    def resolve(self, thread_id: str, token: str) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT ciphertext FROM pii_vault WHERE thread_id = ? AND token = ?",
                (thread_id, token),
            ).fetchone()
        if row is None:
            raise UnknownTokenError(token)
        return self._fernet.decrypt(row[0]).decode()

    def tokens(self, thread_id: str) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT token FROM pii_vault WHERE thread_id = ?", (thread_id,)
            ).fetchall()
        return [r[0] for r in rows]

    def clear(self, thread_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM pii_vault WHERE thread_id = ?", (thread_id,))
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()
