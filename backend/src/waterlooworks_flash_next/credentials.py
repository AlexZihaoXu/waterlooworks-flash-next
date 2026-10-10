"""Admin-managed encrypted WaterlooWorks pair; login and authorized username display.

Never log request bodies, local values, exception diagnostics or database rows.
The encryption key is an independent backend runtime secret, not database data.
"""

import argparse
import base64
import io
import json
import os
import sqlite3
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from cryptography.fernet import Fernet
from dotenv.parser import parse_stream
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from waterlooworks_flash_next.runtime_secrets import (
    is_private_owned_file,
    read_runtime_secret,
)


class CredentialError(Exception):
    """Fixed diagnostics only."""


class CredentialPair(BaseModel):
    model_config = ConfigDict(
        strict=True, extra="forbid", hide_input_in_errors=True, frozen=True
    )
    username: SecretStr = Field(min_length=1, max_length=256, repr=False, exclude=True)
    password: SecretStr = Field(min_length=1, max_length=1024, repr=False, exclude=True)

    @field_validator("username")
    @classmethod
    def nonblank_username(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("Username is required.")
        return value

    def __repr__(self) -> str:
        return "CredentialPair(<redacted>)"


class CredentialReplacement(CredentialPair):
    # Request-only mask guard: never reinterpret or invalidate a stored v12 pair.
    @field_validator("password")
    @classmethod
    def reject_placeholder(cls, value: SecretStr) -> SecretStr:
        if value.get_secret_value() == "••••••••":
            raise ValueError("Enter a replacement password.")
        return value


class CredentialStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    configured: bool
    status: Literal["configured", "not_configured"]
    updated_at: datetime | None


class AdminCredentialStatus(CredentialStatus):
    username: str | None = Field(repr=False)


def migrate_credentials(connection: sqlite3.Connection) -> None:
    # 1,280 supplementary Unicode characters need up to 15,360 JSON escape
    # bytes; Fernet padding, authentication and base64 can exceed 20,000 bytes.
    connection.execute("""
        CREATE TABLE IF NOT EXISTS waterlooworks_credentials (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            ciphertext BLOB NOT NULL CHECK (length(ciphertext) BETWEEN 100 AND 24000),
            updated_at TEXT NOT NULL
        ) STRICT
    """)


def _cipher(value: str | None) -> Fernet:
    if not value or len(value) != 44:
        raise ValueError
    raw = base64.b64decode(value, altchars=b"-_", validate=True)
    if len(raw) != 32 or base64.urlsafe_b64encode(raw).decode() != value:
        raise ValueError
    return Fernet(value.encode("ascii"))


class CredentialStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self, *, writable: bool = False) -> sqlite3.Connection:
        return sqlite3.connect(
            self.path.resolve().as_uri() + ("?mode=rw" if writable else "?mode=ro"),
            uri=True,
            timeout=5,
        )

    def status(self) -> CredentialStatus:
        """Authenticate stored ciphertext without decrypting or exposing identity."""
        connection = None
        try:
            cipher = _cipher(read_runtime_secret("WW_CREDENTIALS_KEY", max_bytes=44))
            connection = self._connect()
            row = connection.execute(
                "SELECT ciphertext, updated_at FROM "
                "waterlooworks_credentials WHERE singleton=1"
            ).fetchone()
            if row:
                cipher.extract_timestamp(row[0])  # Verifies MAC, never decrypts.
            return CredentialStatus(
                configured=row is not None,
                status="configured" if row else "not_configured",
                updated_at=row[1] if row else None,
            )
        except Exception:
            raise CredentialError(
                "WaterlooWorks credentials unavailable; check "
                "admin settings and runtime key."
            ) from None
        finally:
            if connection is not None:
                connection.close()

    def admin_status(self) -> AdminCredentialStatus:
        """Explicit admin-only readback: username only, never the password."""
        connection = None
        try:
            cipher = _cipher(read_runtime_secret("WW_CREDENTIALS_KEY", max_bytes=44))
            connection = self._connect()
            row = connection.execute(
                "SELECT ciphertext, updated_at FROM waterlooworks_credentials "
                "WHERE singleton=1"
            ).fetchone()
            username = None
            if row:
                pair = CredentialPair.model_validate_json(cipher.decrypt(row[0]))
                username = pair.username.get_secret_value()
            return AdminCredentialStatus(
                configured=row is not None,
                status="configured" if row else "not_configured",
                updated_at=row[1] if row else None,
                username=username,
            )
        except Exception:
            raise CredentialError(
                "WaterlooWorks credentials unavailable; check "
                "admin settings and runtime key."
            ) from None
        finally:
            if connection is not None:
                connection.close()

    def require_ready(self) -> None:
        if not self.status().configured:
            raise CredentialError(
                "WaterlooWorks credentials are not configured; "
                "save them in admin settings."
            )

    def save(self, pair: CredentialPair) -> CredentialStatus:
        connection = None
        try:
            cipher = _cipher(read_runtime_secret("WW_CREDENTIALS_KEY", max_bytes=44))
            # Revalidate even internal callers; the pair is one immutable snapshot.
            pair = CredentialPair(username=pair.username, password=pair.password)
            token = cipher.encrypt(
                json.dumps(
                    {
                        "username": pair.username.get_secret_value(),
                        "password": pair.password.get_secret_value(),
                    },
                    ensure_ascii=True,
                ).encode()
            )
            updated_at = datetime.now(UTC)
            connection = self._connect(writable=True)
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT ciphertext FROM waterlooworks_credentials WHERE singleton=1"
                ).fetchone()
                if row:
                    cipher.extract_timestamp(
                        row[0]
                    )  # Never overwrite unreadable state.
                connection.execute(
                    "INSERT INTO waterlooworks_credentials VALUES (1, ?, ?) "
                    "ON CONFLICT(singleton) DO UPDATE SET "
                    "ciphertext=excluded.ciphertext, "
                    "updated_at=excluded.updated_at",
                    (token, updated_at.isoformat()),
                )
            return CredentialStatus(
                configured=True, status="configured", updated_at=updated_at
            )
        except Exception:
            raise CredentialError(
                "WaterlooWorks credentials unavailable; check "
                "admin settings and runtime key."
            ) from None
        finally:
            if connection is not None:
                connection.close()

    def load_for_login(self) -> CredentialPair:
        """One authenticated decryption, before any browser/network activity."""
        connection = None
        try:
            cipher = _cipher(read_runtime_secret("WW_CREDENTIALS_KEY", max_bytes=44))
            connection = self._connect()
            row = connection.execute(
                "SELECT ciphertext FROM waterlooworks_credentials WHERE singleton=1"
            ).fetchone()
            if row is None:
                raise ValueError
            return CredentialPair.model_validate_json(cipher.decrypt(row[0]))
        except Exception:
            raise CredentialError(
                "WaterlooWorks credentials unavailable; check "
                "admin settings and runtime key."
            ) from None
        finally:
            if connection is not None:
                connection.close()


def initialize_key(env_path: Path, database_path: Path) -> None:
    """Explicit offline provisioning only. Never replace a key or touch DB schema.

    Stop the backend and other environment writers first. The exclusive sidecar
    serializes this command; permissions remain the operator's responsibility
    on Windows. Neither existing nor new secret values are printed.
    """
    lock_path = env_path.with_name(env_path.name + ".credentials-key.lock")
    lock: int | None = None
    connection = None
    try:
        if (
            env_path.is_symlink()
            or os.environ.get("WW_CREDENTIALS_KEY") is not None
            or os.environ.get("WW_CREDENTIALS_KEY_FILE") is not None
        ):
            raise ValueError
        connection = sqlite3.connect(
            database_path.resolve().as_uri() + "?mode=ro", uri=True
        )
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (12, 13, 14, 15):
            raise ValueError
        if connection.execute("SELECT 1 FROM waterlooworks_credentials").fetchone():
            raise ValueError
        if (
            version >= 13
            and connection.execute("SELECT 1 FROM openai_credentials").fetchone()
        ):
            raise ValueError
        if (
            version >= 15
            and connection.execute("SELECT 1 FROM ai_credentials").fetchone()
        ):
            raise ValueError
        try:
            lock = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise CredentialError(
                f"Key initialization lock {lock_path.name} already exists. If no "
                "other provisioning run is active, a previous run was interrupted: "
                "remove that sidecar file next to the env file and retry."
            ) from None
        # Exclusive application lock; never run concurrently with other .env editors.
        # Open without following symlinks, then validate the descriptor itself so the
        # earlier path check cannot be raced. Existing files must already be private.
        descriptor = os.open(
            env_path,
            os.O_RDWR
            | os.O_APPEND
            | os.O_CREAT
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_BINARY", 0),
            0o600,
        )
        with os.fdopen(descriptor, "r+b") as output:
            info = os.fstat(output.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or not is_private_owned_file(info)
            ):
                raise CredentialError(
                    "Key initialization refused; the env file must be a private "
                    "regular file (mode 0600, one link, owned by the running user)."
                )
            original = output.read()
            for binding in parse_stream(io.StringIO(original.decode("utf-8"))):
                if binding.error or binding.key in {
                    "WW_CREDENTIALS_KEY",
                    "WW_CREDENTIALS_KEY_FILE",
                }:
                    raise ValueError
            addition = b"" if not original or original.endswith(b"\n") else b"\n"
            addition += b"WW_CREDENTIALS_KEY=" + Fernet.generate_key() + b"\n"
            output.write(addition)
            output.flush()
            os.fsync(output.fileno())
    except CredentialError:
        raise
    except Exception:
        raise CredentialError(
            "Key initialization refused; require schema12 through schema15, no "
            "stored credentials, and no existing key or malformed environment. "
            "No key replacement is supported."
        ) from None
    finally:
        if connection is not None:
            connection.close()
        if lock is not None:
            os.close(lock)
            lock_path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Initialize an absent backend encryption key offline; "
        "never prints secrets."
    )
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    args = parser.parse_args()
    try:
        initialize_key(args.env_file, args.database)
    except CredentialError as error:
        # Every CredentialError message is fixed text without paths or values.
        raise SystemExit(
            f"{error} Existing keys and encrypted credentials are never replaced."
        ) from None
    print(
        "Backend credentials key initialized. Restrict file "
        "access and restart the backend before saving credentials."
    )


if __name__ == "__main__":
    main()
