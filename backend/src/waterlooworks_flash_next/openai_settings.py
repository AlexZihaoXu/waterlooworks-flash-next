"""Encrypted admin configuration only; never an OpenAI client or extraction engine."""

import json
import math
import secrets
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from cryptography.fernet import Fernet
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from waterlooworks_flash_next.app_auth import AuthError, _digest
from waterlooworks_flash_next.credentials import (
    CredentialError,
    CredentialStatus,
    CredentialStore,
    _cipher,
)
from waterlooworks_flash_next.runtime_secrets import read_runtime_secret


class OpenAIKey(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    api_key: SecretStr = Field(min_length=1, max_length=512, repr=False, exclude=True)

    @field_validator("api_key")
    @classmethod
    def nonblank(cls, value: SecretStr) -> SecretStr:
        if (
            not value.get_secret_value().strip()
            or value.get_secret_value() == "••••••••"
        ):
            raise ValueError("Enter a replacement key.")
        return value


class _OpenAIKeyPayload(OpenAIKey):
    # Authenticated purpose binding prevents a copied v12 WW token from being
    # interpreted as a key, including revealing its username/password JSON.
    purpose: Literal["openai-api-key-v1"]


def _decrypt_openai_key(cipher: Fernet, token: bytes) -> SecretStr:
    return _OpenAIKeyPayload.model_validate_json(cipher.decrypt(token)).api_key


class RevealChallenge(BaseModel):
    challenge: str = Field(repr=False)
    not_before: int
    expires_at: int


class RevealRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    challenge: SecretStr = Field(min_length=43, max_length=43, repr=False)
    id_token: SecretStr = Field(min_length=1, max_length=16384, repr=False)


class RevealedKey(BaseModel):
    # Intentionally serialized ONLY by the successful, freshly verified reveal POST.
    api_key: str = Field(repr=False)


def migrate_openai_settings(connection: sqlite3.Connection) -> None:
    connection.execute("""
        CREATE TABLE openai_credentials (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            ciphertext BLOB NOT NULL CHECK(length(ciphertext) BETWEEN 100 AND 4000),
            updated_at TEXT NOT NULL
        ) STRICT
    """)
    connection.execute("""
        CREATE TABLE openai_reauth_challenges (
            nonce_hash TEXT PRIMARY KEY NOT NULL CHECK(length(nonce_hash)=64),
            session_hash TEXT NOT NULL UNIQUE
                REFERENCES app_sessions(token_hash) ON DELETE CASCADE,
            uid TEXT NOT NULL REFERENCES app_users(uid),
            issued_at REAL NOT NULL,
            not_before INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        ) STRICT
    """)
    connection.execute(
        "CREATE INDEX openai_reauth_expiry ON openai_reauth_challenges(expires_at)"
    )


class OpenAISettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self, *, writable: bool = False) -> sqlite3.Connection:
        connection = CredentialStore(self.path)._connect(writable=writable)
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def load_for_inference(self) -> SecretStr:
        """Backend explicit inference only; never use for metadata or CLI output."""
        connection = None
        try:
            cipher = _cipher(read_runtime_secret("WW_CREDENTIALS_KEY", max_bytes=44))
            connection = self._connect()
            row = connection.execute(
                "SELECT ciphertext FROM openai_credentials WHERE singleton=1"
            ).fetchone()
            if row is None:
                raise ValueError
            return _decrypt_openai_key(cipher, row[0])
        except Exception:
            raise CredentialError(
                "OpenAI inference unavailable; check admin settings and runtime key."
            ) from None
        finally:
            if connection is not None:
                connection.close()

    def status(self) -> CredentialStatus:
        connection = None
        try:
            cipher = _cipher(read_runtime_secret("WW_CREDENTIALS_KEY", max_bytes=44))
            connection = self._connect()
            row = connection.execute(
                "SELECT ciphertext, updated_at FROM openai_credentials "
                "WHERE singleton=1"
            ).fetchone()
            if row:
                _decrypt_openai_key(cipher, row[0])
            return CredentialStatus(
                configured=row is not None,
                status="configured" if row else "not_configured",
                updated_at=row[1] if row else None,
            )
        except Exception:
            raise CredentialError(
                "OpenAI settings unavailable; check the backend key."
            ) from None
        finally:
            if connection is not None:
                connection.close()

    def save(self, key: OpenAIKey) -> CredentialStatus:
        connection = None
        try:
            cipher = _cipher(read_runtime_secret("WW_CREDENTIALS_KEY", max_bytes=44))
            key = OpenAIKey(api_key=key.api_key)
            token = cipher.encrypt(
                json.dumps(
                    {
                        "purpose": "openai-api-key-v1",
                        "api_key": key.api_key.get_secret_value(),
                    },
                    ensure_ascii=False,
                ).encode()
            )
            updated_at = datetime.now(UTC)
            connection = self._connect(writable=True)
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT ciphertext FROM openai_credentials WHERE singleton=1"
                ).fetchone()
                if row:
                    _decrypt_openai_key(cipher, row[0])
                connection.execute(
                    "INSERT INTO openai_credentials VALUES(1, ?, ?) "
                    "ON CONFLICT(singleton) DO UPDATE SET "
                    "ciphertext=excluded.ciphertext, updated_at=excluded.updated_at",
                    (token, updated_at.isoformat()),
                )
                # Replacement invalidates pending reveals of the preceding value.
                connection.execute("DELETE FROM openai_reauth_challenges")
            return CredentialStatus(
                configured=True, status="configured", updated_at=updated_at
            )
        except Exception:
            raise CredentialError(
                "OpenAI settings unavailable; check the backend key."
            ) from None
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _require_current(
        connection: sqlite3.Connection, cookie: str | None, uid: str, now: float
    ) -> None:
        row = connection.execute(
            "SELECT 1 FROM app_sessions s JOIN app_users u ON u.uid=s.uid "
            "WHERE s.token_hash=? AND s.uid=? AND u.role='admin' "
            "AND s.expires_at>?",
            (_digest(cookie), uid, now),
        ).fetchone()
        if row is None:
            raise AuthError("Reveal verification was not accepted.")

    def prepare_reveal(
        self, cookie: str | None, uid: str, *, now: float | None = None
    ) -> RevealChallenge:
        nonce = secrets.token_urlsafe(32)
        connection = self._connect(writable=True)
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                now = time.time() if now is None else now
                not_before = math.floor(now) + 1
                expires = math.floor(now) + 300
                self._require_current(connection, cookie, uid, now)
                connection.execute(
                    "DELETE FROM openai_reauth_challenges "
                    "WHERE expires_at<=? OR session_hash=?",
                    (now, _digest(cookie)),
                )
                if (
                    connection.execute(
                        "SELECT count(*) FROM openai_reauth_challenges"
                    ).fetchone()[0]
                    >= 1000
                ):
                    raise AuthError("Reveal verification is busy.")
                connection.execute(
                    "INSERT INTO openai_reauth_challenges VALUES (?, ?, ?, ?, ?, ?)",
                    (_digest(nonce), _digest(cookie), uid, now, not_before, expires),
                )
            return RevealChallenge(
                challenge=nonce, not_before=not_before, expires_at=expires
            )
        finally:
            connection.close()

    @staticmethod
    def _challenge(
        connection: sqlite3.Connection,
        cookie: str | None,
        uid: str,
        nonce: str,
        now: float,
    ) -> tuple[int, int]:
        OpenAISettingsStore._require_current(connection, cookie, uid, now)
        row = connection.execute(
            "SELECT not_before, expires_at FROM openai_reauth_challenges "
            "WHERE nonce_hash=? AND session_hash=? AND uid=? AND expires_at>?",
            (_digest(nonce), _digest(cookie), uid, now),
        ).fetchone()
        if row is None:
            raise AuthError("Reveal verification was not accepted.")
        return int(row[0]), int(row[1])

    def validate_challenge(
        self, cookie: str | None, uid: str, nonce: str, *, now: float | None = None
    ) -> None:
        connection = self._connect()
        try:
            self._challenge(
                connection, cookie, uid, nonce, time.time() if now is None else now
            )
        finally:
            connection.close()

    def consume_reveal(
        self,
        cookie: str | None,
        uid: str,
        nonce: str,
        auth_time: int,
        token_expiry: int,
        *,
        now: float | None = None,
    ) -> str:
        """Internal primitive: UID/auth_time/expiry MUST come from FirebaseVerifier.

        The write lock serializes logout/replacement with authorization, consumption
        and decryption. No user bootstrap, cookie issuance or session renewal occurs.
        """
        connection = self._connect(writable=True)
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                now = time.time() if now is None else now
                not_before, _ = self._challenge(connection, cookie, uid, nonce, now)
                if (
                    type(auth_time) is not int
                    or not not_before <= auth_time <= now
                    or token_expiry <= now
                ):
                    raise AuthError("Reveal verification was not accepted.")
                connection.execute(
                    "DELETE FROM openai_reauth_challenges WHERE nonce_hash=?",
                    (_digest(nonce),),
                )
                # Commit consumption even when ciphertext is missing or unreadable.
                result = None
                try:
                    cipher = _cipher(
                        read_runtime_secret("WW_CREDENTIALS_KEY", max_bytes=44)
                    )
                    row = connection.execute(
                        "SELECT ciphertext FROM openai_credentials WHERE singleton=1"
                    ).fetchone()
                    if row:
                        result = _decrypt_openai_key(cipher, row[0]).get_secret_value()
                except Exception:
                    pass
            if result is None:
                raise CredentialError(
                    "OpenAI settings unavailable; check the backend key."
                )
            return result
        finally:
            connection.close()
