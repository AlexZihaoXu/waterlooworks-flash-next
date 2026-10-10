"""Empty SQLite/session primitives only; no provider responses or key material."""

import base64
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from waterlooworks_flash_next.api import create_app
from waterlooworks_flash_next.app_auth import (
    AuthError,
    BootstrapOwner,
    BootstrapRefused,
    SessionStore,
    decode_service_account,
)
from waterlooworks_flash_next.database import open_database


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "!",
        "e30=\n",
        "e30=",
        base64.b64encode(b'{"project_id":"other"}').decode(),
    ],
)
def test_invalid_service_account_is_sanitized(value: str | None) -> None:
    with pytest.raises(AuthError, match="Authentication configuration unavailable"):
        decode_service_account(value)


def test_first_admin_is_atomic_and_persistent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WW_BOOTSTRAP_ADMIN_EMAIL", raising=False)
    monkeypatch.setenv("WW_BOOTSTRAP_ADMIN_UID", "uid-a")
    path = tmp_path / "db.sqlite3"
    open_database(path).close()
    store = SessionStore(path)
    cookies = [store.prepare(None, now=100) for _ in range(2)]
    with ThreadPoolExecutor(2) as pool:
        roles = list(
            pool.map(
                lambda cookie: store.issue(cookie, "uid-a", 500, now=100).role,
                cookies,
            )
        )
    assert roles == ["admin", "admin"]
    member = store.prepare(None, now=100)
    assert store.issue(member, "uid-b", 500, now=100).role == "member"
    open_database(path).close()
    assert SessionStore(path).issue(cookies[0], "uid-a", 500, now=101).role == "admin"
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM app_users").fetchone()[0] == 2
        assert all(cookie not in str(list(connection.iterdump())) for cookie in cookies)


def _user_count(path: Path) -> int:
    with sqlite3.connect(path) as connection:
        return int(connection.execute("SELECT count(*) FROM app_users").fetchone()[0])


@pytest.mark.parametrize(
    ("pins", "uid", "email"),
    [
        ({}, "owner-uid", "owner@example.invalid"),
        ({"WW_BOOTSTRAP_ADMIN_UID": "owner-uid"}, "other-uid", None),
        ({"WW_BOOTSTRAP_ADMIN_UID": ""}, "owner-uid", None),
        ({"WW_BOOTSTRAP_ADMIN_EMAIL": "owner@example.invalid"}, "owner-uid", None),
        (
            {"WW_BOOTSTRAP_ADMIN_EMAIL": "owner@example.invalid"},
            "owner-uid",
            "other@example.invalid",
        ),
        (
            {
                "WW_BOOTSTRAP_ADMIN_UID": "owner-uid",
                "WW_BOOTSTRAP_ADMIN_EMAIL": "owner@example.invalid",
            },
            "owner-uid",
            "other@example.invalid",
        ),
    ],
)
def test_empty_database_refuses_unpinned_first_user(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pins: dict[str, str],
    uid: str,
    email: str | None,
) -> None:
    for name in ("WW_BOOTSTRAP_ADMIN_UID", "WW_BOOTSTRAP_ADMIN_EMAIL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in pins.items():
        monkeypatch.setenv(name, value)
    path = tmp_path / "db.sqlite3"
    open_database(path).close()
    store = SessionStore(path)
    cookie = store.prepare(None, now=100)
    with pytest.raises(BootstrapRefused) as error:
        store.issue(cookie, uid, 500, email=email, now=100)
    assert str(error.value) == "First administrator must be the configured owner."
    assert uid not in str(error.value)
    assert _user_count(path) == 0
    assert store.lookup(cookie, now=101) is None


@pytest.mark.parametrize(
    ("pins", "email"),
    [
        ({"WW_BOOTSTRAP_ADMIN_UID": "owner-uid"}, None),
        (
            {"WW_BOOTSTRAP_ADMIN_EMAIL": " Owner@Example.INVALID "},
            "owner@example.invalid",
        ),
        (
            {
                "WW_BOOTSTRAP_ADMIN_UID": "owner-uid",
                "WW_BOOTSTRAP_ADMIN_EMAIL": "owner@example.invalid",
            },
            "OWNER@example.invalid",
        ),
    ],
)
def test_pinned_owner_bootstraps_admin_then_others_are_members(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pins: dict[str, str],
    email: str | None,
) -> None:
    for name in ("WW_BOOTSTRAP_ADMIN_UID", "WW_BOOTSTRAP_ADMIN_EMAIL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in pins.items():
        monkeypatch.setenv(name, value)
    path = tmp_path / "db.sqlite3"
    open_database(path).close()
    store = SessionStore(path)
    cookie = store.prepare(None, now=100)
    assert store.issue(cookie, "owner-uid", 500, email=email, now=100).role == "admin"
    # Existing admin: the pin no longer matters and later users are members.
    for name in pins:
        monkeypatch.delenv(name)
    other = store.prepare(None, now=100)
    assert store.issue(other, "other-uid", 500, now=100).role == "member"
    assert _user_count(path) == 2


def test_expiry_logout_and_delayed_exchange_cannot_resurrect(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    open_database(path).close()
    store = SessionStore(path, bootstrap=BootstrapOwner(uid="uid"))
    cookie = store.prepare(None, now=100)
    assert store.lookup(cookie, now=100) is None
    store.issue(cookie, "uid", 150, now=100)
    assert store.lookup(cookie, now=149) is not None
    assert store.lookup(cookie, now=150) is None
    with pytest.raises(AuthError):
        store.issue(cookie, "uid", 500, now=151)
    cookie = store.prepare(None, now=200)
    store.logout(cookie)
    with pytest.raises(AuthError):
        store.issue(cookie, "uid", 500, now=201)


@pytest.mark.parametrize("configured", [None, "!", "e30="])
def test_missing_config_fails_closed_without_users(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured: str | None
) -> None:
    if configured is None:
        monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT", raising=False)
    else:
        monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT", configured)
    path = tmp_path / "db.sqlite3"
    with TestClient(create_app(path), base_url="https://localhost") as client:
        headers = {"origin": "https://localhost", "x-ww-request": "1"}
        assert client.get("/api/workspace").status_code == 503
        assert (
            client.post("/api/auth/prepare", json={}, headers=headers).status_code
            == 503
        )
        assert (
            client.post(
                "/api/auth/exchange", json={"id_token": "invalid"}, headers=headers
            ).status_code
            == 503
        )
        with sqlite3.connect(path) as connection:
            assert (
                connection.execute("SELECT count(*) FROM app_users").fetchone()[0] == 0
            )
