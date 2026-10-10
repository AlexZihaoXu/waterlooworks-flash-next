"""Empty SQLite/session primitives only; no provider responses or key material."""

import base64
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from waterlooworks_flash_next.api import create_app
from waterlooworks_flash_next.app_auth import (
    ROTATION_GRACE_SECONDS,
    SESSION_ABSOLUTE_LIFETIME,
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
                lambda cookie: store.issue(
                    cookie, "uid-a", 500, auth_time=100, now=100
                ),
                cookies,
            )
        )
    assert [issued.identity.role for issued in roles] == ["admin", "admin"]
    member = store.prepare(None, now=100)
    member_role = store.issue(member, "uid-b", 500, auth_time=100, now=100)
    assert member_role.identity.role == "member"
    open_database(path).close()
    renewed = SessionStore(path).issue(
        roles[0].cookie, "uid-a", 500, auth_time=100, now=101
    )
    assert renewed.identity.role == "admin"
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
        store.issue(cookie, uid, 500, auth_time=100, email=email, now=100)
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
    owner = store.issue(cookie, "owner-uid", 500, auth_time=100, email=email, now=100)
    assert owner.identity.role == "admin"
    # Existing admin: the pin no longer matters and later users are members.
    for name in pins:
        monkeypatch.delenv(name)
    other = store.prepare(None, now=100)
    member = store.issue(other, "other-uid", 500, auth_time=100, now=100)
    assert member.identity.role == "member"
    assert _user_count(path) == 2


def test_expiry_logout_and_delayed_exchange_cannot_resurrect(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    open_database(path).close()
    store = SessionStore(path, bootstrap=BootstrapOwner(uid="uid"))
    cookie = store.prepare(None, now=100)
    assert store.lookup(cookie, now=100) is None
    cookie = store.issue(cookie, "uid", 150, auth_time=100, now=100).cookie
    assert store.lookup(cookie, now=149) is not None
    assert store.lookup(cookie, now=150) is None
    with pytest.raises(AuthError):
        store.issue(cookie, "uid", 500, auth_time=100, now=151)
    cookie = store.prepare(None, now=200)
    store.logout(cookie)
    with pytest.raises(AuthError):
        store.issue(cookie, "uid", 500, auth_time=200, now=201)


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


def test_login_rotates_prepared_token_and_renewal_rotates_again(
    tmp_path: Path,
) -> None:
    path = tmp_path / "db.sqlite3"
    open_database(path).close()
    store = SessionStore(path, bootstrap=BootstrapOwner(uid="uid"))
    prepared = store.prepare(None, now=100)
    issued = store.issue(prepared, "uid", 5000, auth_time=100, now=100)
    # The pre-authentication value never becomes the authenticated session.
    assert issued.cookie != prepared
    assert store.lookup(prepared, now=101) is None
    assert not store.prepared(prepared)
    assert store.lookup(issued.cookie, now=101) == issued.identity
    renewed = store.issue(issued.cookie, "uid", 5000, auth_time=100, now=200)
    assert renewed.cookie not in (issued.cookie, prepared)
    assert store.lookup(renewed.cookie, now=201) == renewed.identity
    # In-flight requests/tabs get a short grace on the replaced value only.
    assert store.lookup(issued.cookie, now=200 + ROTATION_GRACE_SECONDS - 1)
    assert store.lookup(issued.cookie, now=200 + ROTATION_GRACE_SECONDS) is None
    with sqlite3.connect(path) as connection:
        dump = str(list(connection.iterdump()))
        assert all(value not in dump for value in (prepared, renewed.cookie))


def test_absolute_session_lifetime_requires_fresh_google_login(
    tmp_path: Path,
) -> None:
    assert SESSION_ABSOLUTE_LIFETIME == 12 * 3600
    path = tmp_path / "db.sqlite3"
    open_database(path).close()
    store = SessionStore(path, bootstrap=BootstrapOwner(uid="uid"))
    start = 1_000_000
    cookie = store.prepare(None, now=start)
    renewed = store.issue(cookie, "uid", start + 3600, auth_time=start, now=start)
    near_end = start + SESSION_ABSOLUTE_LIFETIME - 600
    # Hourly-style renewals of the live session, each with a refreshed ID token.
    for now in range(start + 3000, near_end + 1, 3000):
        renewed = store.issue(
            renewed.cookie, "uid", now + 3600, auth_time=start, now=now
        )
    renewed = store.issue(
        renewed.cookie, "uid", near_end + 3600, auth_time=start, now=near_end
    )
    # Renewal never extends beyond the original Google authentication bound.
    assert renewed.identity.expires_at == start + SESSION_ABSOLUTE_LIFETIME
    with pytest.raises(AuthError, match="Authentication session expired"):
        store.issue(
            renewed.cookie,
            "uid",
            start + SESSION_ABSOLUTE_LIFETIME + 3600,
            auth_time=start,
            now=start + SESSION_ABSOLUTE_LIFETIME,
        )
    fresh = store.prepare(None, now=near_end)
    for auth_time in (near_end - SESSION_ABSOLUTE_LIFETIME, near_end + 1):
        with pytest.raises(AuthError):
            store.issue(fresh, "uid", near_end + 60, auth_time=auth_time, now=near_end)
