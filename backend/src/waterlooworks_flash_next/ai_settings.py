"""Admin AI configuration and encrypted provider credentials; never a model client.

Non-secret settings are a draft plus an explicitly activated copy. Saving the
draft never contacts a provider or switches extraction. Secrets (OAuth tokens,
custom server token) are Fernet-encrypted with the independent backend key and
purpose-bound to their slot; the OpenAI API key keeps its schema13 singleton.
"""

import json
import re
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from cryptography.fernet import Fernet
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

from waterlooworks_flash_next.credentials import (
    CredentialError,
    CredentialStore,
    _cipher,
)
from waterlooworks_flash_next.runtime_secrets import read_runtime_secret

AccessMode = Literal["openai_api_key", "chatgpt_signin", "codex_device", "custom"]
CredentialSlot = Literal["chatgpt_signin", "codex_device", "custom_token"]
ACCESS_MODES: tuple[AccessMode, ...] = (
    "openai_api_key",
    "chatgpt_signin",
    "codex_device",
    "custom",
)
MODEL_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}$"
EFFORT_PATTERN = r"^[a-z][a-z0-9_-]{0,23}$"
FIELD_PATTERN = r"^[a-z][a-z0-9_]{0,39}$"
RESERVED_REQUEST_FIELDS = frozenset(
    {
        "model",
        "messages",
        "tools",
        "tool_choice",
        "stream",
        "stream_options",
        "max_tokens",
        "max_completion_tokens",
        "response_format",
        "store",
    }
)
PROVIDER_BASE_URLS: dict[str, str] = {
    "openai_api_key": "https://api.openai.com/v1",
    "chatgpt_signin": "https://api.openai.com/v1",
    "codex_device": "https://chatgpt.com/backend-api",
}
PROVIDER_IDS: dict[str, str] = {
    "openai_api_key": "openai",
    "chatgpt_signin": "openai",
    "codex_device": "openai-codex",
    "custom": "custom",
}
MODE_LABELS: dict[str, str] = {
    "openai_api_key": "OpenAI API key",
    "chatgpt_signin": "Sign in with ChatGPT",
    "codex_device": "Codex device login",
    "custom": "Custom server",
}


class AIConfig(BaseModel):
    """Non-secret AI configuration. Validation errors never echo input values."""

    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, hide_input_in_errors=True
    )
    access_mode: AccessMode = "openai_api_key"
    model_id: str | None = Field(default=None, pattern=MODEL_PATTERN)
    reasoning_effort: str | None = Field(default=None, pattern=EFFORT_PATTERN)
    parallel_streams: int = Field(default=1, ge=1, le=8)
    postings_per_run: int = Field(default=500, ge=1, le=10_000)
    max_turns: int = Field(default=6, ge=1, le=24)
    max_tool_calls: int = Field(default=12, ge=1, le=48)
    max_output_tokens: int = Field(default=4096, ge=256, le=65_536)
    request_timeout_seconds: int = Field(default=120, ge=10, le=600)
    request_limit_per_run: int = Field(default=1000, ge=1, le=10_000)
    custom_base_url: str | None = Field(default=None, min_length=8, max_length=2048)
    custom_context_window: int | None = Field(default=None, ge=1024, le=10_000_000)
    custom_reasoning_field: str | None = Field(default=None, pattern=FIELD_PATTERN)
    custom_disable_with: dict[str, str | bool | int | None] | None = None

    @field_validator("custom_reasoning_field")
    @classmethod
    def not_reserved(cls, value: str | None) -> str | None:
        if value is not None and value in RESERVED_REQUEST_FIELDS:
            raise ValueError("Reasoning field is reserved.")
        return value

    @field_validator("custom_disable_with")
    @classmethod
    def bounded_disable(
        cls, value: dict[str, str | bool | int | None] | None
    ) -> dict[str, str | bool | int | None] | None:
        if value is None:
            return None
        if len(value) > 4 or any(
            not re.fullmatch(FIELD_PATTERN, key)
            or key in RESERVED_REQUEST_FIELDS
            or (isinstance(item, str) and len(item) > 40)
            for key, item in value.items()
        ):
            raise ValueError("Disable parameters are invalid.")
        return value

    @model_validator(mode="after")
    def mode_fields(self) -> "AIConfig":
        if self.access_mode != "custom" and (
            self.custom_reasoning_field is not None
            or self.custom_disable_with is not None
        ):
            raise ValueError("Custom reasoning metadata requires custom mode.")
        return self

    @property
    def provider_id(self) -> str:
        return PROVIDER_IDS[self.access_mode]

    @property
    def endpoint(self) -> str:
        if self.access_mode == "custom":
            return (self.custom_base_url or "").rstrip("/")
        return PROVIDER_BASE_URLS[self.access_mode]

    @property
    def is_zero_cost(self) -> bool:
        return self.access_mode != "openai_api_key"

    @property
    def pricing_version(self) -> str | None:
        return {
            "chatgpt_signin": "subscription",
            "codex_device": "subscription",
            "custom": "custom-free",
        }.get(self.access_mode)

    def label(self) -> str:
        return f"{MODE_LABELS[self.access_mode]} / {self.model_id or 'no model'}"


class ActivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    confirm: Literal[True]


class CustomToken(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    token: SecretStr = Field(min_length=1, max_length=4096, repr=False, exclude=True)

    @field_validator("token")
    @classmethod
    def printable(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if raw == "••••••••" or any(not 33 <= ord(ch) <= 126 for ch in raw):
            raise ValueError("Enter a valid replacement token.")
        return value


class OAuthCredential(BaseModel):
    """pi-ai OAuth credential shape (ChatGPT adds clientId/scopes, Codex accountId)."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    type: Literal["oauth"]
    access: str = Field(min_length=1, max_length=16_384, repr=False)
    refresh: str = Field(min_length=1, max_length=16_384, repr=False)
    expires: int | float = Field(gt=0, lt=1e14)
    clientId: str | None = Field(default=None, min_length=1, max_length=512)
    scopes: list[str] | None = Field(default=None, max_length=32)
    accountId: str | None = Field(
        default=None, min_length=1, max_length=512, repr=False
    )

    def public(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class CredentialSummary(BaseModel):
    configured: bool
    updated_at: datetime | None = None
    expires_at: datetime | None = None


class AISettingsView(BaseModel):
    draft: AIConfig
    active: AIConfig | None
    activated_at: datetime | None
    active_label: str | None
    credentials: dict[str, CredentialSummary]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _decrypt_slot(cipher: Fernet, slot: str, token: bytes) -> dict[str, Any]:
    payload = json.loads(cipher.decrypt(token))
    if (
        not isinstance(payload, dict)
        or set(payload) != {"purpose", "credential"}
        or payload["purpose"] != f"ai-credential-v1:{slot}"
        or not isinstance(payload["credential"], dict)
    ):
        raise ValueError("Credential purpose mismatch.")
    return dict(payload["credential"])


class AISettingsStore:
    """Single-process store; `_locks` serialize credential read-modify-write."""

    _locks: dict[str, threading.Lock] = {
        slot: threading.Lock() for slot in ("chatgpt_signin", "codex_device")
    }

    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self, *, writable: bool = False) -> sqlite3.Connection:
        connection = CredentialStore(self.path)._connect(writable=writable)
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _cipher() -> Fernet:
        return _cipher(read_runtime_secret("WW_CREDENTIALS_KEY", max_bytes=44))

    # ----- non-secret settings -------------------------------------------------

    def _ensure(self, connection: sqlite3.Connection) -> sqlite3.Row | tuple[Any, ...]:
        row = connection.execute(
            "SELECT device_id,draft_json,active_json,activated_at "
            "FROM ai_settings WHERE singleton=1"
        ).fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO ai_settings(singleton,device_id,draft_json,updated_at) "
                "VALUES(1,?,?,?) ON CONFLICT(singleton) DO NOTHING",
                (str(uuid4()), AIConfig().model_dump_json(), _now()),
            )
            row = connection.execute(
                "SELECT device_id,draft_json,active_json,activated_at "
                "FROM ai_settings WHERE singleton=1"
            ).fetchone()
        return tuple(row)

    def device_id(self) -> str:
        """Stable per-installation UUID for Sign in with ChatGPT (agent host id)."""
        connection = self._connect(writable=True)
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                return str(self._ensure(connection)[0])
        finally:
            connection.close()

    def read(self) -> tuple[AIConfig, AIConfig | None, str | None]:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT draft_json,active_json,activated_at FROM ai_settings "
                "WHERE singleton=1"
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return AIConfig(), None, None
        try:
            draft = AIConfig.model_validate_json(row[0])
        except ValidationError:
            draft = AIConfig()
        try:
            active = AIConfig.model_validate_json(row[1]) if row[1] else None
        except ValidationError:
            active = None
        return draft, active, row[2]

    def active(self) -> AIConfig | None:
        return self.read()[1]

    def save_draft(self, config: AIConfig) -> None:
        """Persist only; never validates reachability or invokes a model."""
        connection = self._connect(writable=True)
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                self._ensure(connection)
                connection.execute(
                    "UPDATE ai_settings SET draft_json=?,updated_at=? "
                    "WHERE singleton=1",
                    (config.model_dump_json(), _now()),
                )
        finally:
            connection.close()

    def activate(self) -> AIConfig:
        """Explicit admin action: copy a complete draft into the active slot."""
        draft = self.read()[0]
        if draft.model_id is None:
            raise CredentialError("Choose a model before activating.")
        if draft.access_mode == "custom" and (
            draft.custom_base_url is None or draft.custom_context_window is None
        ):
            raise CredentialError("Custom server settings are incomplete.")
        if draft.access_mode != "custom" and not self.has_credential(draft.access_mode):
            raise CredentialError("Connect this access mode before activating.")
        connection = self._connect(writable=True)
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                self._ensure(connection)
                now = _now()
                connection.execute(
                    "UPDATE ai_settings SET active_json=?,activated_at=?,updated_at=? "
                    "WHERE singleton=1",
                    (draft.model_dump_json(), now, now),
                )
        finally:
            connection.close()
        return draft

    def deactivate(self) -> None:
        connection = self._connect(writable=True)
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                self._ensure(connection)
                connection.execute(
                    "UPDATE ai_settings SET active_json=NULL,activated_at=NULL,"
                    "updated_at=? WHERE singleton=1",
                    (_now(),),
                )
        finally:
            connection.close()

    # ----- encrypted credentials ----------------------------------------------

    def _store_slot(
        self,
        connection: sqlite3.Connection,
        slot: str,
        credential: dict[str, Any],
        metadata: dict[str, Any],
    ) -> None:
        token = self._cipher().encrypt(
            json.dumps(
                {"purpose": f"ai-credential-v1:{slot}", "credential": credential},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        )
        connection.execute(
            "INSERT INTO ai_credentials VALUES(?,?,?,?) ON CONFLICT(slot) DO UPDATE "
            "SET ciphertext=excluded.ciphertext,metadata_json=excluded.metadata_json,"
            "updated_at=excluded.updated_at",
            (slot, token, json.dumps(metadata), _now()),
        )

    def save_oauth(self, slot: CredentialSlot, credential: OAuthCredential) -> None:
        if slot == "custom_token":
            raise CredentialError("Credential slot is invalid.")
        try:
            connection = self._connect(writable=True)
        except Exception:
            raise CredentialError("AI credentials unavailable.") from None
        try:
            with self._locks[slot], connection:
                connection.execute("BEGIN IMMEDIATE")
                self._store_slot(
                    connection,
                    slot,
                    credential.public(),
                    {"expires_at": int(credential.expires)},
                )
        except CredentialError:
            raise
        except Exception:
            raise CredentialError("AI credentials unavailable.") from None
        finally:
            connection.close()

    def save_custom_token(self, token: CustomToken) -> None:
        try:
            connection = self._connect(writable=True)
            try:
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    self._store_slot(
                        connection,
                        "custom_token",
                        {"type": "api_key", "key": token.token.get_secret_value()},
                        {},
                    )
            finally:
                connection.close()
        except Exception:
            raise CredentialError("AI credentials unavailable.") from None

    def delete(self, slot: CredentialSlot) -> None:
        try:
            connection = self._connect(writable=True)
            try:
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute(
                        "DELETE FROM ai_credentials WHERE slot=?", (slot,)
                    )
            finally:
                connection.close()
        except Exception:
            raise CredentialError("AI credentials unavailable.") from None

    def _load_slot(
        self, connection: sqlite3.Connection, slot: str
    ) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT ciphertext FROM ai_credentials WHERE slot=?", (slot,)
        ).fetchone()
        if row is None:
            return None
        return _decrypt_slot(self._cipher(), slot, row[0])

    def load_slot(self, slot: str) -> dict[str, Any] | None:
        """Backend inference only: decrypted credential for the sidecar RPC."""
        try:
            connection = self._connect()
            try:
                return self._load_slot(connection, slot)
            finally:
                connection.close()
        except Exception:
            raise CredentialError("AI credentials unavailable.") from None

    def has_credential(self, mode: str) -> bool:
        if mode == "openai_api_key":
            from waterlooworks_flash_next.openai_settings import OpenAISettingsStore

            try:
                OpenAISettingsStore(self.path).load_for_inference()
                return True
            except CredentialError:
                return False
        try:
            return self.load_slot(mode) is not None
        except CredentialError:
            return False

    def summaries(self) -> dict[str, CredentialSummary]:
        from waterlooworks_flash_next.openai_settings import OpenAISettingsStore

        result: dict[str, CredentialSummary] = {}
        try:
            status = OpenAISettingsStore(self.path).status()
            result["openai_api_key"] = CredentialSummary(
                configured=status.configured, updated_at=status.updated_at
            )
        except CredentialError:
            result["openai_api_key"] = CredentialSummary(configured=False)
        try:
            cipher = self._cipher()
            connection = self._connect()
            try:
                rows = {
                    row[0]: row
                    for row in connection.execute(
                        "SELECT slot,ciphertext,metadata_json,updated_at "
                        "FROM ai_credentials"
                    )
                }
            finally:
                connection.close()
        except Exception:
            raise CredentialError("AI credentials unavailable.") from None
        for slot in ("chatgpt_signin", "codex_device", "custom_token"):
            row = rows.get(slot)
            if row is None:
                result[slot] = CredentialSummary(configured=False)
                continue
            try:
                cipher.extract_timestamp(row[1])  # Verify MAC without decrypting.
                metadata = json.loads(row[2])
                expires = metadata.get("expires_at")
                result[slot] = CredentialSummary(
                    configured=True,
                    updated_at=datetime.fromisoformat(row[3]),
                    expires_at=datetime.fromtimestamp(expires / 1000, UTC)
                    if isinstance(expires, int)
                    else None,
                )
            except Exception:
                result[slot] = CredentialSummary(configured=False)
        return result

    def view(self) -> AISettingsView:
        draft, active, activated = self.read()
        return AISettingsView(
            draft=draft,
            active=active,
            activated_at=datetime.fromisoformat(activated) if activated else None,
            active_label=active.label() if active else None,
            credentials=self.summaries(),
        )

    # ----- sidecar credential RPC ---------------------------------------------

    def credential_for_provider(
        self, config: AIConfig, provider_id: str
    ) -> dict[str, Any] | None:
        """Only the active mode's own provider id ever resolves a secret."""
        if provider_id != config.provider_id:
            return None
        if config.access_mode == "openai_api_key":
            from waterlooworks_flash_next.openai_settings import OpenAISettingsStore

            key = OpenAISettingsStore(self.path).load_for_inference()
            return {"type": "api_key", "key": key.get_secret_value()}
        if config.access_mode == "custom":
            return self.load_slot("custom_token")
        return self.load_slot(config.access_mode)

    def lock(self, config: AIConfig, timeout: float = 30.0) -> threading.Lock:
        if config.access_mode not in self._locks:
            raise CredentialError("Credential cannot be modified.")
        lock = self._locks[config.access_mode]
        if not lock.acquire(timeout=timeout):
            raise CredentialError("Credential is busy.")
        return lock

    def commit_locked(self, config: AIConfig, credential: OAuthCredential) -> None:
        """Persist a rotated OAuth credential while the caller holds `lock()`."""
        slot = config.access_mode
        if slot not in ("chatgpt_signin", "codex_device"):
            raise CredentialError("Credential cannot be modified.")
        if slot == "chatgpt_signin" and credential.clientId is None:
            raise CredentialError("Credential shape is invalid.")
        if slot == "codex_device" and credential.accountId is None:
            raise CredentialError("Credential shape is invalid.")
        try:
            connection = self._connect(writable=True)
            try:
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    self._store_slot(
                        connection,
                        slot,
                        credential.public(),
                        {"expires_at": int(credential.expires)},
                    )
            finally:
                connection.close()
        except Exception:
            raise CredentialError("AI credentials unavailable.") from None
