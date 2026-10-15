"""Encrypted per-user Discord webhook storage and strict URL validation.

Webhook URLs here are generated structural values (random tokens), never real
destinations; nothing contacts Discord.
"""

import secrets
import sqlite3
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from waterlooworks_flash_next.approvals import ApprovalStore
from waterlooworks_flash_next.credentials import CredentialError, initialize_key
from waterlooworks_flash_next.database import open_database
from waterlooworks_flash_next.discord_webhooks import (
    MAX_WEBHOOKS_PER_USER,
    WebhookDuplicate,
    WebhookLimitReached,
    WebhookNotFound,
    WebhookStore,
    WebhookUnavailable,
    WebhookUrlError,
    normalize_webhook_url,
)


def webhook_url(host: str = "discord.com", webhook_id: str = "1234567890123") -> str:
    return f"https://{host}/api/webhooks/{webhook_id}/{secrets.token_urlsafe(48)}"


@pytest.fixture
def path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WW_CREDENTIALS_KEY", Fernet.generate_key().decode())
    database = tmp_path / "db.sqlite3"
    connection = open_database(database)
    with connection:
        connection.execute("INSERT INTO app_users VALUES ('u1','admin',1)")
        connection.execute("INSERT INTO app_users VALUES ('u2','member',1)")
    connection.close()
    ApprovalStore(database).approve("u1", "u2")
    return database


@pytest.mark.parametrize(
    "host", ["discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com"]
)
def test_accepted_hosts_normalise_to_discord_com(host: str) -> None:
    token = secrets.token_urlsafe(48)
    normalized, webhook_id = normalize_webhook_url(
        f"  https://{host}/api/webhooks/987654321/{token}\n"
    )
    assert normalized == f"https://discord.com/api/webhooks/987654321/{token}"
    assert webhook_id == "987654321"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "http://discord.com/api/webhooks/1/abc",
        "https://discord.com.example/api/webhooks/1/abc",
        "https://example.com/api/webhooks/1/abc",
        "https://evil.discord.com/api/webhooks/1/abc",
        "https://user@discord.com/api/webhooks/1/abc",
        "https://user:pw@discord.com/api/webhooks/1/abc",
        "https://discord.com:443/api/webhooks/1/abc",
        "https://discord.com/api/webhooks/1/abc?wait=true",
        "https://discord.com/api/webhooks/1/abc?",
        "https://discord.com/api/webhooks/1/abc#x",
        "https://discord.com/api/webhooks/1/abc/",
        "https://discord.com/api/webhooks/1/abc/github",
        "https://discord.com/api/webhooks/x1/abc",
        "https://discord.com/api/webhooks/1/ab%63",
        "https://discord.com/api/webhooks/1/ab.c",
        "https://discord.com/api/webhooks//abc",
        "https://discord.com/api/v10/webhooks/1/abc",
        "https://discord.com/api/webhooks/1/a b",
        "https://discord.com\\@example.com/api/webhooks/1/abc",
        "https://discord.com/api/webhooks/1/" + "a" * 300,
        "https://DISCORD.COM/api/webhooks/1/abc\x00",
    ],
)
def test_rejected_urls_never_echo_input(raw: str) -> None:
    with pytest.raises(WebhookUrlError) as error:
        normalize_webhook_url(raw)
    assert raw.strip() == "" or raw not in str(error.value)
    assert "abc" not in str(error.value)


def test_add_encrypts_and_lists_only_safe_metadata(path: Path) -> None:
    store = WebhookStore(path)
    url = webhook_url(webhook_id="111122223333")
    added = store.add("u1", "  Jobs channel  ", url, now=1000)
    assert added.label == "Jobs channel"
    assert added.hint == "…3333"
    assert added.enabled is True
    assert added.last_status is None and added.last_sent_at is None
    listed = store.for_user("u1")
    assert [item.id for item in listed] == [added.id]
    assert url not in listed[0].model_dump_json()
    assert store.for_user("u2") == []
    connection = sqlite3.connect(path)
    row = connection.execute("SELECT ciphertext, hint FROM discord_webhooks").fetchone()
    connection.close()
    token = url.rsplit("/", 1)[1]
    assert token.encode() not in row[0] and b"discord" not in row[0]
    target = store.target("u1", added.id)
    assert target.url.get_secret_value() == url
    assert url not in repr(target)


def test_ciphertext_is_bound_to_its_owner(path: Path) -> None:
    store = WebhookStore(path)
    mine = store.add("u1", "Mine", webhook_url(), now=1000)
    theirs = store.add("u2", "Theirs", webhook_url(webhook_id="55"), now=1000)
    connection = sqlite3.connect(path)
    with connection:
        stolen = connection.execute(
            "SELECT ciphertext FROM discord_webhooks WHERE id=?", (theirs.id,)
        ).fetchone()[0]
        connection.execute(
            "UPDATE discord_webhooks SET ciphertext=? WHERE id=?", (stolen, mine.id)
        )
    connection.close()
    with pytest.raises(WebhookUnavailable):
        store.target("u1", mine.id)


def test_users_cannot_see_or_change_each_others_webhooks(path: Path) -> None:
    store = WebhookStore(path)
    added = store.add("u1", "Mine", webhook_url(), now=1000)
    with pytest.raises(WebhookNotFound):
        store.update("u2", added.id, enabled=False)
    with pytest.raises(WebhookNotFound):
        store.delete("u2", added.id)
    with pytest.raises(WebhookNotFound):
        store.target("u2", added.id)
    assert store.for_user("u1")[0].enabled is True


def test_update_and_delete(path: Path) -> None:
    store = WebhookStore(path)
    added = store.add("u1", "Mine", webhook_url(), now=1000)
    updated = store.update("u1", added.id, label="Renamed", enabled=False)
    assert (updated.label, updated.enabled) == ("Renamed", False)
    assert store.update("u1", added.id, enabled=True).label == "Renamed"
    store.delete("u1", added.id)
    assert store.for_user("u1") == []
    with pytest.raises(WebhookNotFound):
        store.delete("u1", added.id)


@pytest.mark.parametrize("label", ["", "   ", "x" * 61, "bad\nlabel", "tab\tlabel"])
def test_labels_are_bounded(path: Path, label: str) -> None:
    with pytest.raises(ValueError):
        WebhookStore(path).add("u1", label, webhook_url())


def test_per_user_limit_and_duplicates(path: Path) -> None:
    store = WebhookStore(path)
    first = webhook_url()
    store.add("u1", "One", first)
    with pytest.raises(WebhookDuplicate):
        store.add("u1", "Again", first.replace("discord.com", "discordapp.com"))
    # Another user may add the same destination.
    store.add("u2", "Shared", first)
    for index in range(MAX_WEBHOOKS_PER_USER - 1):
        store.add("u1", f"Hook {index}", webhook_url(webhook_id=str(100 + index)))
    with pytest.raises(WebhookLimitReached):
        store.add("u1", "Too many", webhook_url(webhook_id="999"))
    assert len(store.for_user("u1")) == MAX_WEBHOOKS_PER_USER


def test_missing_key_never_stores_plaintext(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WW_CREDENTIALS_KEY")
    store = WebhookStore(path)
    with pytest.raises(WebhookUnavailable):
        store.add("u1", "Mine", webhook_url())
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT count(*) FROM discord_webhooks").fetchone() == (
        0,
    )
    connection.close()


def test_send_results_update_status_and_gone_disables(path: Path) -> None:
    store = WebhookStore(path)
    added = store.add("u1", "Mine", webhook_url(), now=1000)
    store.record(added.id, "ok", now=2000)
    item = store.for_user("u1")[0]
    assert item.last_status == "ok" and item.last_sent_at is not None
    assert item.last_error_at is None
    store.record(added.id, "failed", now=3000)
    item = store.for_user("u1")[0]
    assert item.last_status == "failed" and item.enabled is True
    assert item.last_error_at is not None and item.last_sent_at is not None
    store.record(added.id, "gone", now=4000)
    item = store.for_user("u1")[0]
    assert item.last_status == "gone" and item.enabled is False


def test_enabled_targets_skip_disabled_and_undecryptable_rows(path: Path) -> None:
    store = WebhookStore(path)
    on = store.add("u1", "On", webhook_url(webhook_id="1"))
    off = store.add("u2", "Off", webhook_url(webhook_id="2"))
    broken = store.add("u2", "Broken", webhook_url(webhook_id="3"))
    store.update("u2", off.id, enabled=False)
    connection = sqlite3.connect(path)
    with connection:
        connection.execute(
            "UPDATE discord_webhooks SET ciphertext=? WHERE id=?",
            (Fernet(Fernet.generate_key()).encrypt(b"x" * 80), broken.id),
        )
    connection.close()
    targets = store.enabled_targets()
    assert [target.id for target in targets] == [on.id]
    assert [item.last_status for item in store.for_user("u2")] == [None, "failed"]


def test_enabled_targets_skip_users_who_are_not_approved(path: Path) -> None:
    store = WebhookStore(path)
    mine = store.add("u1", "Admin", webhook_url(webhook_id="1"))
    theirs = store.add("u2", "Member", webhook_url(webhook_id="2"))
    assert [target.id for target in store.enabled_targets()] == [mine.id, theirs.id]
    connection = sqlite3.connect(path)
    with connection:
        connection.execute(
            "UPDATE user_approvals SET status='pending', parent_uid=NULL, "
            "decided_by=NULL, decided_at=NULL WHERE uid='u2'"
        )
    connection.close()
    assert [target.id for target in store.enabled_targets()] == [mine.id]
    # The row stays (not failed, not disabled); only removal deletes it.
    assert [item.enabled for item in store.for_user("u2")] == [True]
    assert [item.last_status for item in store.for_user("u2")] == [None]


def test_key_initialization_refuses_existing_webhook_ciphertext(
    path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    connection = sqlite3.connect(path)
    with connection:
        connection.execute("DELETE FROM waterlooworks_credentials")
    connection.close()
    WebhookStore(path).add("u1", "Mine", webhook_url())
    monkeypatch.delenv("WW_CREDENTIALS_KEY")
    env = tmp_path / ".env"
    with pytest.raises(CredentialError):
        initialize_key(env, path)
    assert not env.exists()
