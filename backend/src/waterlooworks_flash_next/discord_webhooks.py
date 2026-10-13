"""Per-user Discord webhook destinations, encrypted at rest.

A webhook URL is a bearer secret: anyone holding it can post to the channel.
URLs are validated strictly, normalised to ``discord.com``, Fernet-encrypted
with the independent backend ``WW_CREDENTIALS_KEY`` and purpose-bound to the
owning user. Only a short hint derived from the (non-secret) webhook ID is kept
in clear. URLs never appear in responses, logs or exception messages; without
a configured key nothing is stored and no plaintext fallback exists.
"""

import json
import re
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from cryptography.fernet import Fernet
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from waterlooworks_flash_next.credentials import _cipher
from waterlooworks_flash_next.runtime_secrets import read_runtime_secret

MAX_WEBHOOKS_PER_USER = 10
MAX_LABEL_LENGTH = 60
MAX_URL_LENGTH = 300
ACCEPTED_HOSTS = frozenset(
    {"discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com"}
)
_URL = re.compile(
    r"\Ahttps://(?P<host>[A-Za-z.]+)/api/webhooks/"
    r"(?P<id>[0-9]{1,25})/(?P<token>[A-Za-z0-9_-]{1,128})\Z",
    re.ASCII,
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")
WebhookStatus = Literal["ok", "failed", "gone"]


class WebhookUrlError(ValueError):
    """Fixed message only; never carries the submitted URL."""

    def __init__(self) -> None:
        super().__init__(
            "Enter a Discord webhook URL like "
            "https://discord.com/api/webhooks/<id>/<token>."
        )


class WebhookUnavailable(Exception):
    """The backend key is missing/invalid or stored ciphertext is unreadable."""

    def __init__(self) -> None:
        super().__init__(
            "Discord webhooks are unavailable; the backend encryption key is "
            "not configured."
        )


class WebhookNotFound(Exception):
    pass


class WebhookLimitReached(Exception):
    pass


class WebhookDuplicate(Exception):
    pass


def normalize_webhook_url(raw: str) -> tuple[str, str]:
    """Return the canonical ``discord.com`` URL and the webhook ID.

    Accepts only ``https://{discord.com|discordapp.com|ptb.discord.com|
    canary.discord.com}/api/webhooks/{numeric id}/{token}`` with no userinfo,
    port, query, fragment, trailing slash or percent-encoding.
    """
    if not isinstance(raw, str):
        raise WebhookUrlError
    value = raw.strip()
    if len(value) > MAX_URL_LENGTH:
        raise WebhookUrlError
    match = _URL.match(value)
    if match is None or match["host"].lower() not in ACCEPTED_HOSTS:
        raise WebhookUrlError
    return (
        f"https://discord.com/api/webhooks/{match['id']}/{match['token']}",
        match["id"],
    )


def clean_label(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Label is required.")
    label = value.strip()
    if not 1 <= len(label) <= MAX_LABEL_LENGTH or _CONTROL.search(label):
        raise ValueError("Label must be 1 to 60 characters on one line.")
    return label


class DiscordWebhook(BaseModel):
    """Safe metadata only; never the URL, its token or ciphertext."""

    model_config = ConfigDict(extra="forbid")
    id: int
    label: str
    hint: str = Field(description="Last four digits of the webhook ID, e.g. …1234.")
    enabled: bool
    created_at: str = Field(description="UTC ISO 8601.")
    last_sent_at: str | None = Field(
        description="UTC ISO 8601 of the latest successful delivery."
    )
    last_status: WebhookStatus | None = Field(
        description="Result of the latest delivery attempt; gone means Discord "
        "reported the webhook deleted, so it was disabled."
    )
    last_error_at: str | None = Field(
        description="UTC ISO 8601 of the latest failed delivery."
    )


@dataclass(frozen=True)
class WebhookTarget:
    id: int
    uid: str
    hint: str
    url: SecretStr = field(repr=False)
    created_at: int = 0


def _iso(epoch: int | None) -> str | None:
    return datetime.fromtimestamp(epoch, UTC).isoformat() if epoch else None


def _view(row: sqlite3.Row) -> DiscordWebhook:
    return DiscordWebhook(
        id=row["id"],
        label=row["label"],
        hint=row["hint"],
        enabled=bool(row["enabled"]),
        created_at=_iso(row["created_at"]) or "",
        last_sent_at=_iso(row["last_sent_at"]),
        last_status=row["last_status"],
        last_error_at=_iso(row["last_error_at"]),
    )


_COLUMNS = "id,label,hint,enabled,created_at,last_sent_at,last_status,last_error_at"


class WebhookStore:
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

    @staticmethod
    def _cipher() -> Fernet:
        try:
            return _cipher(read_runtime_secret("WW_CREDENTIALS_KEY", max_bytes=44))
        except Exception:
            raise WebhookUnavailable from None

    def available(self) -> bool:
        """Whether the backend key is configured (never decrypts anything)."""
        try:
            self._cipher()
        except WebhookUnavailable:
            return False
        return True

    @staticmethod
    def _purpose(uid: str) -> str:
        return f"discord-webhook-v1:{uid}"

    def _encrypt(self, cipher: Fernet, uid: str, url: str) -> bytes:
        return cipher.encrypt(
            json.dumps(
                {"purpose": self._purpose(uid), "url": url},
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode()
        )

    def _decrypt(self, cipher: Fernet, uid: str, token: bytes) -> SecretStr:
        try:
            payload = json.loads(cipher.decrypt(token))
            if (
                not isinstance(payload, dict)
                or set(payload) != {"purpose", "url"}
                or payload["purpose"] != self._purpose(uid)
            ):
                raise ValueError
            normalized, _ = normalize_webhook_url(payload["url"])
            return SecretStr(normalized)
        except Exception:
            raise WebhookUnavailable from None

    def for_user(self, uid: str) -> list[DiscordWebhook]:
        connection = self._connect(readonly=True)
        try:
            rows = connection.execute(
                f"SELECT {_COLUMNS} FROM discord_webhooks WHERE uid=? ORDER BY id",
                (uid,),
            ).fetchall()
        finally:
            connection.close()
        return [_view(row) for row in rows]

    def _one(
        self, connection: sqlite3.Connection, uid: str, webhook_id: int
    ) -> DiscordWebhook:
        row = connection.execute(
            f"SELECT {_COLUMNS} FROM discord_webhooks WHERE uid=? AND id=?",
            (uid, webhook_id),
        ).fetchone()
        if row is None:
            raise WebhookNotFound
        return _view(row)

    def add(
        self, uid: str, label: str, url: str, *, now: int | None = None
    ) -> DiscordWebhook:
        label = clean_label(label)
        normalized, webhook_id = normalize_webhook_url(url)
        cipher = self._cipher()
        now = int(time.time()) if now is None else now
        ciphertext = self._encrypt(cipher, uid, normalized)
        connection = self._connect()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT ciphertext FROM discord_webhooks WHERE uid=?", (uid,)
                ).fetchall()
                if len(existing) >= MAX_WEBHOOKS_PER_USER:
                    raise WebhookLimitReached
                for row in existing:
                    try:
                        stored = self._decrypt(cipher, uid, row[0])
                    except WebhookUnavailable:
                        continue
                    if stored.get_secret_value() == normalized:
                        raise WebhookDuplicate
                cursor = connection.execute(
                    "INSERT INTO discord_webhooks(uid,label,ciphertext,hint,enabled,"
                    "created_at) VALUES (?,?,?,?,1,?)",
                    (uid, label, ciphertext, "…" + webhook_id[-4:], now),
                )
                return self._one(connection, uid, int(cursor.lastrowid or 0))
        finally:
            connection.close()

    def update(
        self,
        uid: str,
        webhook_id: int,
        *,
        label: str | None = None,
        enabled: bool | None = None,
    ) -> DiscordWebhook:
        if label is not None:
            label = clean_label(label)
        connection = self._connect()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                current = self._one(connection, uid, webhook_id)
                connection.execute(
                    "UPDATE discord_webhooks SET label=?, enabled=? "
                    "WHERE uid=? AND id=?",
                    (
                        current.label if label is None else label,
                        int(current.enabled if enabled is None else enabled),
                        uid,
                        webhook_id,
                    ),
                )
                return self._one(connection, uid, webhook_id)
        finally:
            connection.close()

    def delete(self, uid: str, webhook_id: int) -> None:
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    "DELETE FROM discord_webhooks WHERE uid=? AND id=?",
                    (uid, webhook_id),
                )
            if cursor.rowcount != 1:
                raise WebhookNotFound
        finally:
            connection.close()

    def view(self, uid: str, webhook_id: int) -> DiscordWebhook:
        connection = self._connect(readonly=True)
        try:
            return self._one(connection, uid, webhook_id)
        finally:
            connection.close()

    def target(self, uid: str, webhook_id: int) -> WebhookTarget:
        """Decrypt one of the caller's own webhooks for an explicit test send."""
        connection = self._connect(readonly=True)
        try:
            row = connection.execute(
                "SELECT id,uid,hint,ciphertext,created_at FROM discord_webhooks "
                "WHERE uid=? AND id=?",
                (uid, webhook_id),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise WebhookNotFound
        cipher = self._cipher()
        return WebhookTarget(
            id=row["id"],
            uid=row["uid"],
            hint=row["hint"],
            url=self._decrypt(cipher, uid, row["ciphertext"]),
            created_at=row["created_at"],
        )

    def enabled_targets(self) -> list[WebhookTarget]:
        """Every enabled webhook of a current app user, decrypted for one run.

        Raises ``WebhookUnavailable`` when the key is missing. Rows that fail
        authenticated decryption are skipped and recorded as failed.
        """
        connection = self._connect(readonly=True)
        try:
            rows = connection.execute(
                "SELECT w.id,w.uid,w.hint,w.ciphertext,w.created_at "
                "FROM discord_webhooks w "
                "JOIN app_users u ON u.uid=w.uid WHERE w.enabled=1 ORDER BY w.id"
            ).fetchall()
        finally:
            connection.close()
        if not rows:
            return []
        cipher = self._cipher()
        targets: list[WebhookTarget] = []
        for row in rows:
            try:
                url = self._decrypt(cipher, row["uid"], row["ciphertext"])
            except WebhookUnavailable:
                self.record(row["id"], "failed")
                continue
            targets.append(
                WebhookTarget(
                    id=row["id"],
                    uid=row["uid"],
                    hint=row["hint"],
                    url=url,
                    created_at=row["created_at"],
                )
            )
        return targets

    def record(
        self, webhook_id: int, status: WebhookStatus, *, now: int | None = None
    ) -> None:
        """Store one delivery result; ``gone`` also disables the webhook."""
        now = int(time.time()) if now is None else now
        connection = self._connect()
        try:
            with connection:
                if status == "ok":
                    connection.execute(
                        "UPDATE discord_webhooks SET last_status='ok', "
                        "last_sent_at=? WHERE id=?",
                        (now, webhook_id),
                    )
                else:
                    connection.execute(
                        "UPDATE discord_webhooks SET last_status=?, last_error_at=?, "
                        "enabled=CASE WHEN ?='gone' THEN 0 ELSE enabled END "
                        "WHERE id=?",
                        (status, now, status, webhook_id),
                    )
        finally:
            connection.close()

    def announced(self, webhook_ids: list[int]) -> dict[int, set[str]]:
        """Postings each webhook already announced (or skipped as stale)."""
        result: dict[int, set[str]] = {webhook_id: set() for webhook_id in webhook_ids}
        if not webhook_ids:
            return result
        connection = self._connect(readonly=True)
        try:
            marks = ",".join("?" * len(webhook_ids))
            for webhook_id, posting_id in connection.execute(
                "SELECT webhook_id,posting_id FROM discord_webhook_announced "
                f"WHERE webhook_id IN ({marks})",
                webhook_ids,
            ):
                result[webhook_id].add(posting_id)
        finally:
            connection.close()
        return result

    def mark_announced(
        self, webhook_id: int, posting_ids: Iterable[str], *, now: int | None = None
    ) -> None:
        """Never announce these postings to this webhook again.

        A webhook deleted meanwhile gets no rows (and no foreign-key error).
        """
        now = int(time.time()) if now is None else now
        rows = [(webhook_id, posting_id, now, webhook_id) for posting_id in posting_ids]
        if not rows:
            return
        connection = self._connect()
        try:
            with connection:
                connection.executemany(
                    "INSERT OR IGNORE INTO discord_webhook_announced"
                    "(webhook_id,posting_id,announced_at) SELECT ?,?,? WHERE EXISTS "
                    "(SELECT 1 FROM discord_webhooks WHERE id=?)",
                    rows,
                )
        finally:
            connection.close()
