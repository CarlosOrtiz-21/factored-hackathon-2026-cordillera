"""Token vault: reversible pseudonymization scoped per conversation thread.

`tokenize` swaps a real value for a token like <TARJETA_1>. The mapping lives here,
never in graph state, logs, or prompts. `resolve` goes the other way; only tools and
`tool_executor` receive a vault reference, so agents cannot de-tokenize anything.

Tokens are scoped to a thread: a token from one conversation is meaningless (and
rejected) in another, which blocks cross-customer token replay.
"""

import re
import threading
from dataclasses import dataclass, field
from typing import Protocol

TOKEN_PATTERN = re.compile(r"<([A-Z]+)_(\d+)>")


class UnknownTokenError(KeyError):
    """The token doesn't exist in this thread's vault."""


def normalize(label: str, value: str) -> str:
    """Canonical form, so '4111 1111 1111 1111' and '4111-1111-1111-1111' share one token."""
    if label == "EMAIL":
        return value.strip().lower()
    if label == "PERSONA":
        return " ".join(value.split()).casefold()
    return "".join(c for c in value if c.isalnum()).upper()


class TokenVault(Protocol):
    def tokenize(self, thread_id: str, label: str, value: str) -> str: ...
    def resolve(self, thread_id: str, token: str) -> str: ...
    def tokens(self, thread_id: str) -> list[str]: ...
    def clear(self, thread_id: str) -> None: ...


@dataclass
class _ThreadVault:
    by_key: dict[tuple[str, str], str] = field(default_factory=dict)  # (label, norm) -> token
    by_token: dict[str, str] = field(default_factory=dict)  # token -> original value
    counters: dict[str, int] = field(default_factory=dict)  # label -> last index


class InMemoryTokenVault:
    """Process-local vault. Production would use Redis with TTL + encryption at rest."""

    def __init__(self) -> None:
        self._threads: dict[str, _ThreadVault] = {}
        self._lock = threading.Lock()

    def tokenize(self, thread_id: str, label: str, value: str) -> str:
        key = (label, normalize(label, value))
        with self._lock:
            vault = self._threads.setdefault(thread_id, _ThreadVault())
            if key in vault.by_key:
                return vault.by_key[key]
            index = vault.counters.get(label, 0) + 1
            vault.counters[label] = index
            token = f"<{label}_{index}>"
            vault.by_key[key] = token
            vault.by_token[token] = value
            return token

    def resolve(self, thread_id: str, token: str) -> str:
        with self._lock:
            vault = self._threads.get(thread_id)
            if vault is None or token not in vault.by_token:
                raise UnknownTokenError(token)
            return vault.by_token[token]

    def tokens(self, thread_id: str) -> list[str]:
        with self._lock:
            vault = self._threads.get(thread_id)
            return list(vault.by_token) if vault else []

    def clear(self, thread_id: str) -> None:
        with self._lock:
            self._threads.pop(thread_id, None)
