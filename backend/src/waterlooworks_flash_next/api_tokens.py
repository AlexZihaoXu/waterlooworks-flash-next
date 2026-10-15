"""Show-once per-user API tokens for agents acting on a member's behalf.

Only a SHA-256 digest and a short display hint are stored. The plaintext is
returned exactly once by ``reset`` and must never be logged, echoed in errors
or written anywhere else. Authentication always rejoins ``app_users`` and
the admission row, so a removed, declined or not-yet-approved user (or a
changed role) is reflected on the very next request.
"""

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TOKEN_PREFIX = "wwf_"
# secrets.token_urlsafe(32) is always 43 URL-safe characters.
TOKEN_PATTERN = re.compile(r"\Awwf_[A-Za-z0-9_-]{43}\Z", re.ASCII)
# ``last_used_at`` is bumped at most this often to keep hot paths write-free.
LAST_USED_THROTTLE_SECONDS = 60


class ApiTokenStatus(BaseModel):
    """Safe metadata only; never the token or its digest."""

    model_config = ConfigDict(extra="forbid")
    configured: bool
    hint: str | None = Field(
        default=None, description="Prefix and last four characters, e.g. wwf_…ab12."
    )
    created_at: str | None = Field(default=None, description="UTC ISO 8601.")
    last_used_at: str | None = Field(
        default=None,
        description="UTC ISO 8601 of the latest recorded use (minute resolution).",
    )


@dataclass(frozen=True)
class IssuedToken:
    token: str = field(repr=False)
    hint: str
    created_at: str


@dataclass(frozen=True)
class AgentIdentity:
    uid: str
    role: Literal["admin", "member"]


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat()


class TokenStore:
    """Short-lived connections per call; the database is already migrated."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self, *, readonly: bool = False) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path.resolve().as_uri() + ("?mode=ro" if readonly else "?mode=rw"),
            uri=True,
            timeout=5,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        if readonly:
            connection.execute("PRAGMA query_only=ON")
        return connection

    def status(self, uid: str) -> ApiTokenStatus:
        connection = self._connect(readonly=True)
        try:
            row = connection.execute(
                "SELECT hint, created_at, last_used_at FROM api_tokens WHERE uid=?",
                (uid,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return ApiTokenStatus(configured=False)
        return ApiTokenStatus(
            configured=True,
            hint=row["hint"],
            created_at=_iso(row["created_at"]),
            last_used_at=_iso(row["last_used_at"])
            if row["last_used_at"] is not None
            else None,
        )

    def reset(self, uid: str, *, now: int | None = None) -> IssuedToken:
        """Mint a fresh token, replacing (invalidating) any existing one."""
        now = int(time.time()) if now is None else now
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        hint = f"{TOKEN_PREFIX}…{token[-4:]}"
        connection = self._connect()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("DELETE FROM api_tokens WHERE uid=?", (uid,))
                connection.execute(
                    "INSERT INTO api_tokens(uid, token_hash, hint, created_at, "
                    "last_used_at) VALUES (?, ?, ?, ?, NULL)",
                    (uid, token_digest(token), hint, now),
                )
        finally:
            connection.close()
        return IssuedToken(token=token, hint=hint, created_at=_iso(now))

    def revoke(self, uid: str) -> bool:
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    "DELETE FROM api_tokens WHERE uid=?", (uid,)
                )
            return cursor.rowcount == 1
        finally:
            connection.close()

    def authenticate(
        self, token: str | None, *, now: int | None = None
    ) -> AgentIdentity | None:
        """Resolve a presented token to a current app user, or None.

        Membership, role and approval are re-read on every call; only an
        approved user's token resolves. The stored digest is compared in
        constant time after the indexed lookup.
        """
        if not isinstance(token, str) or TOKEN_PATTERN.match(token) is None:
            return None
        now = int(time.time()) if now is None else now
        digest = token_digest(token)
        connection = self._connect()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT t.uid, t.token_hash, u.role FROM api_tokens t "
                    "JOIN app_users u ON u.uid=t.uid "
                    "JOIN user_approvals a ON a.uid=t.uid "
                    "WHERE t.token_hash=? AND a.status='approved'",
                    (digest,),
                ).fetchone()
                if row is None or not hmac.compare_digest(row["token_hash"], digest):
                    return None
                connection.execute(
                    "UPDATE api_tokens SET last_used_at=? WHERE uid=? AND "
                    "(last_used_at IS NULL OR last_used_at<=?)",
                    (now, row["uid"], now - LAST_USED_THROTTLE_SECONDS),
                )
                return AgentIdentity(uid=row["uid"], role=row["role"])
        finally:
            connection.close()
