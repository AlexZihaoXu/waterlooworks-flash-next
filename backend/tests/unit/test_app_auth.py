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


def test_first_admin_is_atomic_and_persistent(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    open_database(path).close()
    store = SessionStore(path)
    cookies = [store.prepare(None, now=100) for _ in range(2)]
    with ThreadPoolExecutor(2) as pool:
        roles = list(
            pool.map(
                lambda pair: store.issue(pair[0], pair[1], 500, now=100).role,
                zip(cookies, ("uid-a", "uid-b"), strict=True),
            )
        )
    assert sorted(roles) == ["admin", "member"]
    open_database(path).close()
    assert SessionStore(path).issue(cookies[0], "uid-a", 500, now=101).role == roles[0]
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM app_users").fetchone()[0] == 2
        assert all(cookie not in str(list(connection.iterdump())) for cookie in cookies)


def test_expiry_logout_and_delayed_exchange_cannot_resurrect(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    open_database(path).close()
    store = SessionStore(path)
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
