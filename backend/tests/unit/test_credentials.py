"""Random secret primitives and empty databases only; no browser/provider I/O."""

import os
import secrets
import sqlite3
import time
from pathlib import Path

import pytest
from auth_support import session_client
from cryptography.fernet import Fernet
from pydantic import SecretStr, ValidationError

from waterlooworks_flash_next.credentials import (
    CredentialError,
    CredentialPair,
    CredentialStore,
    initialize_key,
)
from waterlooworks_flash_next.database import SCHEMA_VERSION, open_database


def pair() -> CredentialPair:
    return CredentialPair(
        username=SecretStr(secrets.token_urlsafe(24)),
        password=SecretStr(secrets.token_urlsafe(48)),
    )


def test_encrypted_pair_replacement_and_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "db.sqlite3"
    open_database(path).close()
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("WW_CREDENTIALS_KEY", key)
    store = CredentialStore(path)
    assert store.status().configured is False
    first, second = pair(), pair()
    store.save(first)
    assert store.status().configured is True
    assert store.load_for_login() == first
    store.save(second)
    assert store.load_for_login() == second
    with sqlite3.connect(path) as connection:
        token = connection.execute(
            "SELECT ciphertext FROM waterlooworks_credentials"
        ).fetchone()[0]
        assert second.username.get_secret_value().encode() not in token
        assert second.password.get_secret_value().encode() not in token
        assert (
            connection.execute("SELECT count(*) FROM collection_runs").fetchone()[0]
            == 0
        )
    for stored_file in tmp_path.glob("db.sqlite3*"):
        stored_bytes = (
            stored_file.read_bytes()
        )  # This test's random, empty database only.
        for secret_pair in (first, second):
            assert secret_pair.username.get_secret_value().encode() not in stored_bytes
            assert secret_pair.password.get_secret_value().encode() not in stored_bytes
        assert key.encode() not in stored_bytes
    for invalid in (None, "invalid", Fernet.generate_key().decode()):
        if invalid is None:
            monkeypatch.delenv("WW_CREDENTIALS_KEY")
        else:
            monkeypatch.setenv("WW_CREDENTIALS_KEY", invalid)
        for operation in (
            store.status,
            store.load_for_login,
            lambda: store.save(first),
        ):
            with pytest.raises(
                CredentialError, match="WaterlooWorks credentials unavailable"
            ):
                operation()
    monkeypatch.setenv("WW_CREDENTIALS_KEY", key)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE waterlooworks_credentials SET ciphertext=?",
            (token[:-8] + b"xxxxxxxx",),
        )
    with pytest.raises(CredentialError):
        store.save(first)
    with pytest.raises(CredentialError):
        store.load_for_login()


def test_maximum_unicode_pair_round_trips_without_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "unicode.sqlite3"
    open_database(path).close()
    monkeypatch.setenv("WW_CREDENTIALS_KEY", Fernet.generate_key().decode())
    maximum = CredentialPair(
        username=SecretStr("\U0001f512" * 256),
        password=SecretStr("\U0001f512" * 1024),
    )
    store = CredentialStore(path)
    assert store.save(maximum).configured
    assert store.load_for_login() == maximum


def test_pair_validation_is_bounded_and_redacted() -> None:
    value = pair()
    assert value.username.get_secret_value() not in repr(value)
    assert value.model_dump() == {}
    for data in (
        {"username": 2, "password": "x"},
        {"username": " ", "password": "x"},
        {"username": "x", "password": ""},
        {"username": "x" * 257, "password": "x"},
        {"username": "x", "password": "x" * 1025},
        {"username": "x", "password": "x", "extra": secrets.token_urlsafe()},
    ):
        with pytest.raises(ValidationError):
            CredentialPair.model_validate(data)


def test_admin_settings_and_secret_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from waterlooworks_flash_next.app_auth import COOKIE, SessionStore

    path = tmp_path / "db.sqlite3"
    monkeypatch.setenv("WW_CREDENTIALS_KEY", Fernet.generate_key().decode())
    endpoint = "/api/admin/waterlooworks-credentials"
    with session_client(path) as client:
        secret = pair()
        body = {
            "username": secret.username.get_secret_value(),
            "password": secret.password.get_secret_value(),
        }
        assert client.get(endpoint).json() == {
            "configured": False,
            "status": "not_configured",
            "updated_at": None,
            "username": None,
        }
        response = client.post(endpoint, json=body)
        assert response.status_code == 200
        assert set(response.json()) == {"configured", "status", "updated_at"}
        assert response.json()["status"] == "configured"
        assert response.headers["cache-control"] == "no-store"
        assert (
            client.get(endpoint).json()["username"]
            == secret.username.get_secret_value()
        )
        assert secret.password.get_secret_value() not in client.get(endpoint).text
        for public in (
            response.text,
            client.get("/api/workspace").text,
            client.get("/api/refresh/events?once=true").text,
        ):
            assert secret.username.get_secret_value() not in public
            assert secret.password.get_secret_value() not in public
        for invalid in (
            {
                **body,
                secret.password.get_secret_value(): secret.username.get_secret_value(),
            },
            {**body, "password": [secret.password.get_secret_value()]},
        ):
            response = client.post(endpoint, json=invalid)
            assert response.status_code == 422
            assert response.json() == {
                "detail": "Invalid request; input diagnostics suppressed."
            }
        response = client.post(
            endpoint,
            content='{"' + secret.password.get_secret_value(),
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 422
        assert secret.password.get_secret_value() not in response.text
        for headers in (
            {"origin": ""},
            {"origin": "https://evil.invalid"},
            {"x-ww-request": ""},
        ):
            assert client.post(endpoint, json=body, headers=headers).status_code == 403
        assert client.post(endpoint, content="").status_code == 415
        store = SessionStore(path)
        cookie = store.prepare(None)
        now = int(time.time())
        cookie = store.issue(cookie, "member", now + 300, auth_time=now).cookie
        client.cookies.clear()
        client.cookies.set(COOKIE, cookie, domain="localhost.local", path="/api")
        assert client.get(endpoint).status_code == 403
        assert client.post(endpoint, json=body).status_code == 403
        store.logout(cookie)
        assert client.get(endpoint).status_code == 401
        assert client.post(endpoint, json=body).status_code == 401
        expired = store.prepare(None, now=100)
        expired = store.issue(expired, "member", 200, auth_time=100, now=100).cookie
        client.cookies.clear()
        client.cookies.set(COOKIE, expired, domain="localhost.local", path="/api")
        assert client.get(endpoint).status_code == 401
        assert client.post(endpoint, json=body).status_code == 401


def test_explicit_key_initialization_never_overwrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WW_CREDENTIALS_KEY", raising=False)
    path, env = tmp_path / "db.sqlite3", tmp_path / ".env"
    with pytest.raises(CredentialError):
        initialize_key(env, path)
    assert not path.exists() and not env.exists()
    open_database(path).close()
    env.write_text("UNRELATED_SETTING=1\n")
    initialize_key(env, path)
    content = env.read_text()
    assert content.startswith("UNRELATED_SETTING=1\n")
    with pytest.raises(CredentialError):
        initialize_key(env, path)
    assert env.read_text() == content
    env.write_text("WW_CREDENTIALS_KEY=invalid\n")
    with pytest.raises(CredentialError):
        initialize_key(env, path)
    assert env.read_text() == "WW_CREDENTIALS_KEY=invalid\n"
    monkeypatch.setenv("WW_CREDENTIALS_KEY", Fernet.generate_key().decode())
    CredentialStore(path).save(pair())
    monkeypatch.delenv("WW_CREDENTIALS_KEY")
    env.unlink()
    with pytest.raises(CredentialError):
        initialize_key(env, path)
    assert not env.exists()


def test_schema12_additive_preserves_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from waterlooworks_flash_next.app_auth import BootstrapOwner, SessionStore

    path = tmp_path / "db.sqlite3"
    from waterlooworks_flash_next import database

    with monkeypatch.context() as legacy:
        legacy.setattr(database, "SCHEMA_VERSION", 13)
        open_database(path).close()
    store = SessionStore(path, bootstrap=BootstrapOwner(uid="owner"))
    cookie = store.prepare(None)
    now = int(time.time())
    issued = store.issue(cookie, "owner", now + 300, auth_time=now)
    cookie, identity = issued.cookie, issued.identity
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE openai_reauth_challenges")
        connection.execute("DROP TABLE openai_credentials")
        connection.execute("DROP TABLE waterlooworks_credentials")
        connection.execute("PRAGMA user_version=11")
        before = connection.execute(
            "SELECT type,name,sql FROM sqlite_master"
        ).fetchall()
    connection = open_database(path)
    before = [row for row in before if row[1] != "inference_jobs"]
    assert set(before) <= set(
        tuple(row)
        for row in connection.execute("SELECT type,name,sql FROM sqlite_master")
    )
    assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()
    assert store.lookup(cookie) == identity


def test_key_cli_uses_explicit_empty_database_without_printing_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import sys

    from waterlooworks_flash_next.credentials import main

    monkeypatch.delenv("WW_CREDENTIALS_KEY", raising=False)
    path, env = tmp_path / "db.sqlite3", tmp_path / ".env"
    open_database(path).close()
    monkeypatch.setattr(
        sys, "argv", ["credentials", "--database", str(path), "--env-file", str(env)]
    )
    main()
    output = capsys.readouterr()
    assert "Backend credentials key initialized." in output.out
    assert "WW_CREDENTIALS_KEY" not in output.out
    assert not output.err
    original = env.read_bytes()
    with pytest.raises(SystemExit, match="Key initialization refused"):
        main()
    assert env.read_bytes() == original


def test_refresh_readiness_precedes_claim_or_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from waterlooworks_flash_next.refresh import RefreshService

    path = tmp_path / "db.sqlite3"
    open_database(path).close()
    monkeypatch.setenv("WW_CREDENTIALS_KEY", Fernet.generate_key().decode())
    service = RefreshService(path, tmp_path)
    with pytest.raises(CredentialError, match="not configured"):
        service.start()
    assert service.worker is None
    assert not (tmp_path / ".experiment").exists()
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute("SELECT count(*) FROM collection_runs").fetchone()[0]
            == 0
        )
    monkeypatch.delenv("WW_CREDENTIALS_KEY")
    with pytest.raises(CredentialError, match="unavailable"):
        service.start()


def test_login_loads_exactly_one_pair_before_browser_and_retains_guards() -> None:
    import ast
    import inspect

    from waterlooworks_flash_next.login import start_login

    tree = ast.parse(inspect.getsource(start_login))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    loads = [
        node
        for node in calls
        if isinstance(node.func, ast.Attribute) and node.func.attr == "load_for_login"
    ]
    browser = next(
        node for node in calls if ast.unparse(node.func) == "sync_playwright"
    )
    assert len(loads) == 1 and loads[0].lineno < browser.lineno
    assert sum(ast.unparse(node.func) == "require_auth_host" for node in calls) >= 3
    assert ".credentials.json" not in inspect.getsource(start_login)


def test_missing_key_api_fails_closed_without_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WW_CREDENTIALS_KEY", raising=False)
    path = tmp_path / "db.sqlite3"
    endpoint = "/api/admin/waterlooworks-credentials"
    with session_client(path) as client:
        value = pair()
        assert client.get(endpoint).status_code == 503
        assert (
            client.post(
                endpoint,
                json={
                    "username": value.username.get_secret_value(),
                    "password": value.password.get_secret_value(),
                },
            ).status_code
            == 503
        )
        with sqlite3.connect(path) as connection:
            assert (
                connection.execute(
                    "SELECT count(*) FROM waterlooworks_credentials"
                ).fetchone()[0]
                == 0
            )


def test_runtime_environment_debug_guard_runs_before_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from waterlooworks_flash_next.login import LoginFlowError, start_login

    # Record initially absent variables too, so dotenv's runtime changes undo.
    monkeypatch.setenv("DEBUG", "")
    monkeypatch.setenv("PWDEBUG", "")
    monkeypatch.delenv("DEBUG")
    monkeypatch.delenv("PWDEBUG")
    (tmp_path / ".env").write_text("PWDEBUG=1\n")
    with pytest.raises(LoginFlowError, match="Disable DEBUG and PWDEBUG"):
        start_login(tmp_path / "absent.sqlite3", project_root=tmp_path)
    assert "PWDEBUG" not in os.environ
    assert not (tmp_path / "absent.sqlite3").exists()
    assert not (tmp_path / ".experiment").exists()


def test_mask_is_request_only_and_never_invalidates_stored_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from waterlooworks_flash_next.credentials import CredentialReplacement

    path = tmp_path / "empty.sqlite3"
    open_database(path).close()
    monkeypatch.setenv("WW_CREDENTIALS_KEY", Fernet.generate_key().decode())
    value = CredentialPair(
        username=SecretStr(secrets.token_urlsafe()), password=SecretStr("••••••••")
    )
    store = CredentialStore(path)
    store.save(value)
    assert store.load_for_login() == value
    assert store.admin_status().username == value.username.get_secret_value()
    with pytest.raises(ValidationError):
        CredentialReplacement(username=value.username, password=value.password)


def test_initialization_refuses_openai_ciphertext_without_ww_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from waterlooworks_flash_next.openai_settings import OpenAIKey, OpenAISettingsStore

    path, env = tmp_path / "empty.sqlite3", tmp_path / ".env"
    open_database(path).close()
    monkeypatch.setenv("WW_CREDENTIALS_KEY", Fernet.generate_key().decode())
    OpenAISettingsStore(path).save(
        OpenAIKey(api_key=SecretStr(secrets.token_urlsafe(48)))
    )
    monkeypatch.delenv("WW_CREDENTIALS_KEY")
    with pytest.raises(CredentialError):
        initialize_key(env, path)
    assert not env.exists()
