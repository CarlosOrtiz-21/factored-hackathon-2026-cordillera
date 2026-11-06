"""Customer sessions: the trusted proof of identity for every tool call.

A customer id or document number alone never proves identity. A session is issued by
the (mock) identity service after authentication, is a random bearer token, expires,
and can be revoked. The database stores only the token's SHA-256, so a leaked
database doesn't leak usable tokens.
"""

import hashlib
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cardagent.bank.db import BankDB
from cardagent.bank.errors import InvalidSessionError, SessionExpiredError


def utc_now() -> datetime:
    return datetime.now(UTC)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class Session:
    session_id: str
    customer_id: str
    expires_at: datetime


class SessionService:
    def __init__(self, db: BankDB, ttl_minutes: int = 15, clock: Callable[[], datetime] = utc_now):
        self._db = db
        self._ttl = timedelta(minutes=ttl_minutes)
        self._clock = clock

    def issue(self, customer_id: str) -> tuple[str, Session]:
        """Called by the identity service AFTER it authenticated the customer.

        Returns the raw token (shown once to the client) and the session.
        """
        if not self._db.one("SELECT 1 FROM customers WHERE customer_id = ?", (customer_id,)):
            raise InvalidSessionError("unknown customer")
        token = secrets.token_urlsafe(32)
        now = self._clock()
        session = Session(str(uuid.uuid4()), customer_id, now + self._ttl)
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, NULL)",
                (
                    session.session_id,
                    _hash(token),
                    customer_id,
                    now.isoformat(),
                    session.expires_at.isoformat(),
                ),
            )
        return token, session

    def validate(self, token: str) -> Session:
        row = self._db.one(
            "SELECT session_id, customer_id, expires_at, revoked_at FROM sessions "
            "WHERE token_sha256 = ?",
            (_hash(token),),
        )
        if row is None or row["revoked_at"] is not None:
            raise InvalidSessionError("invalid session")
        expires_at = datetime.fromisoformat(row["expires_at"])
        if self._clock() >= expires_at:
            raise SessionExpiredError("session expired")
        return Session(row["session_id"], row["customer_id"], expires_at)

    def revoke(self, token: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE sessions SET revoked_at = ? WHERE token_sha256 = ?",
                (self._clock().isoformat(), _hash(token)),
            )


@dataclass(frozen=True)
class StaffSession:
    staff_id: str
    role: str
    expires_at: datetime


class StaffSessionService:
    """Bank staff (auditors) authenticate separately from customers: a customer token is
    never valid on staff endpoints, so a customer can't approve their own request."""

    def __init__(self, db: BankDB, ttl_minutes: int = 60, clock: Callable[[], datetime] = utc_now):
        self._db, self._ttl, self._clock = db, timedelta(minutes=ttl_minutes), clock

    def issue(self, staff_id: str, role: str) -> tuple[str, StaffSession]:
        token = secrets.token_urlsafe(32)
        session = StaffSession(staff_id, role, self._clock() + self._ttl)
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO staff_sessions VALUES (?, ?, ?, ?)",
                (_hash(token), staff_id, role, session.expires_at.isoformat()),
            )
        return token, session

    def validate(self, token: str) -> StaffSession:
        row = self._db.one(
            "SELECT staff_id, role, expires_at FROM staff_sessions WHERE token_sha256 = ?",
            (_hash(token),),
        )
        if row is None:
            raise InvalidSessionError("invalid staff session")
        expires_at = datetime.fromisoformat(row["expires_at"])
        if self._clock() >= expires_at:
            raise SessionExpiredError("staff session expired")
        return StaffSession(row["staff_id"], row["role"], expires_at)
