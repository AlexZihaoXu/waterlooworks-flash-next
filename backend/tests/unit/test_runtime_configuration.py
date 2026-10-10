"""Credential-free temporary secret and real empty-app transport contracts."""

import base64
import os
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr

from waterlooworks_flash_next.api import create_app
from waterlooworks_flash_next.app_auth import AuthError, AuthTransport, FirebaseVerifier
from waterlooworks_flash_next.credentials import CredentialPair, CredentialStore
from waterlooworks_flash_next.database import open_database
from waterlooworks_flash_next.openai_settings import OpenAIKey, OpenAISettingsStore
from waterlooworks_flash_next.runtime_secrets import (
    RuntimeSecretError,
    read_runtime_secret,
)


def test_runtime_file_is_bounded_and_never_exported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = "WW_TEST_RUNTIME_SECRET"
    path = tmp_path / "secret"
    path.write_bytes(b"primitive\n")
    monkeypatch.setenv(name + "_FILE", str(path))
    assert read_runtime_secret(name, max_bytes=10) == "primitive"
    assert name not in os.environ
    with pytest.raises(RuntimeSecretError, match="Runtime secret unavailable"):
        read_runtime_secret(name, max_bytes=3)
    monkeypatch.setenv(name, "")
    with pytest.raises(RuntimeSecretError) as error:
        read_runtime_secret(name, max_bytes=10)
    assert str(path) not in str(error.value)
    monkeypatch.delenv(name)
    path.write_bytes(b"\xff")
    with pytest.raises(RuntimeSecretError):
        read_runtime_secret(name, max_bytes=10)
    path.unlink()
    with pytest.raises(RuntimeSecretError):
        read_runtime_secret(name, max_bytes=10)


def test_file_key_uses_same_purpose_encrypted_stores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WW_CREDENTIALS_KEY", raising=False)
    path = tmp_path / "key"
    key = Fernet.generate_key()
    path.write_bytes(key + b"\n")
    monkeypatch.setenv("WW_CREDENTIALS_KEY_FILE", str(path))
    database = tmp_path / "empty.sqlite3"
    open_database(database).close()
    credentials = CredentialStore(database)
    pair = CredentialPair(
        username=SecretStr("primitive"), password=SecretStr("primitive")
    )
    credentials.save(pair)
    assert credentials.status().configured
    assert credentials.admin_status().username == "primitive"
    settings = OpenAISettingsStore(database)
    settings.save(OpenAIKey(api_key=SecretStr(base64.urlsafe_b64encode(key).decode())))
    assert settings.status().configured
    assert (
        settings.load_for_inference().get_secret_value()
        == base64.urlsafe_b64encode(key).decode()
    )


@pytest.mark.parametrize(
    "origin",
    [
        "https://*.invalid",
        "https://example.invalid:bad",
        "https://example.invalid:99999",
        "https://example.invalid/",
    ],
)
def test_origin_configuration_is_exact_and_sanitized(
    origin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WW_AUTH_ORIGINS", origin)
    with pytest.raises(
        AuthError, match="Authentication transport configuration unavailable"
    ):
        AuthTransport.from_environment()


@pytest.mark.parametrize(
    "peer",
    [
        "*",
        "0.0.0.0",
        "224.0.0.1",
        "172.30.87.0/29",
        "frontend",
        "127.0.0.1,172.30.87.2",
    ],
)
def test_proxy_configuration_requires_one_concrete_address(
    peer: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WW_AUTH_TRUSTED_PROXY", peer)
    with pytest.raises(AuthError):
        AuthTransport.from_environment()


def test_real_app_trusts_only_exact_peer_proto_host_and_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WW_AUTH_ORIGINS", "https://example.invalid")
    monkeypatch.setenv("WW_AUTH_TRUSTED_PROXY", "172.30.87.2")
    app = create_app(tmp_path / "empty.sqlite3")
    with TestClient(
        app, base_url="http://example.invalid", client=("172.30.87.2", 1234)
    ) as client:
        assert client.get("/doc").status_code == 403
        headers = {"x-forwarded-proto": "https"}
        assert client.get("/doc", headers=headers).status_code == 200
        for extra in (
            {"host": "example.invalid:444"},
            {"host": "localhost"},
            {"host": "["},
            {"origin": "https://other.invalid"},
            {"x-forwarded-proto": "https,http"},
        ):
            assert client.get("/doc", headers=headers | extra).status_code == 403
        assert (
            client.get(
                "/doc",
                headers=[
                    ("x-forwarded-proto", "https"),
                    ("x-forwarded-proto", "https"),
                ],
            ).status_code
            == 403
        )
    with TestClient(
        app, base_url="http://example.invalid", client=("172.30.87.3", 1234)
    ) as client:
        assert (
            client.get(
                "/doc",
                headers={
                    "x-forwarded-proto": "https",
                    "forwarded": "proto=https",
                    "x-forwarded-host": "example.invalid",
                },
            ).status_code
            == 403
        )
    with TestClient(
        app, base_url="http://127.0.0.1", client=("127.0.0.1", 1234)
    ) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/doc").status_code == 403


def test_firebase_runtime_file_failures_are_sanitized_without_sdk_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT", raising=False)
    path = tmp_path / "temporary-input"
    path.write_bytes(b"not-base64\n")
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_FILE", str(path))
    verifier = FirebaseVerifier(tmp_path)
    for ambiguous in (False, True):
        if ambiguous:
            monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT", "")
        with pytest.raises(AuthError) as error:
            verifier.ensure_ready()
        assert str(error.value) == "Authentication configuration unavailable."
        assert verifier._app is None
    verifier.close()


def test_proxy_cookie_remains_secure_without_authentication_or_paid_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from auth_support import configuration_ready

    from waterlooworks_flash_next.app_auth import require_firebase_ready

    monkeypatch.setenv("WW_AUTH_ORIGINS", "https://example.invalid")
    monkeypatch.setenv("WW_AUTH_TRUSTED_PROXY", "172.30.87.2")
    app = create_app(tmp_path / "empty.sqlite3")
    # Existing test-only configuration readiness seam; no identity verification bypass.
    app.dependency_overrides[require_firebase_ready] = configuration_ready
    with TestClient(
        app, base_url="http://example.invalid", client=("172.30.87.2", 1234)
    ) as client:
        response = client.post(
            "/api/auth/prepare",
            json={},
            headers={
                "origin": "https://example.invalid",
                "x-forwarded-proto": "https",
                "x-ww-request": "1",
            },
        )
        assert response.status_code == 200
        assert "; Secure" in response.headers["set-cookie"]
        assert response.headers["cache-control"] == "no-store"
        assert (
            client.get(
                "/doc",
                headers=[
                    ("x-forwarded-proto", "https"),
                    ("origin", "https://example.invalid"),
                    ("origin", "https://example.invalid"),
                ],
            ).status_code
            == 403
        )


def test_local_env_file_is_a_private_lookup_never_exported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from waterlooworks_flash_next.runtime_secrets import runtime_value

    names = ("WW_TEST_LOCAL_SECRET", "WW_TEST_LOCAL_SETTING", "WW_TEST_PRECEDENCE")
    for name in names:
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text(
        "WW_TEST_LOCAL_SECRET=primitive\n"
        "WW_TEST_LOCAL_SETTING=1\n"
        "WW_TEST_PRECEDENCE=file\n"
    )
    monkeypatch.setenv("WW_TEST_PRECEDENCE", "process")
    FirebaseVerifier(tmp_path).load_environment()
    assert not {"WW_TEST_LOCAL_SECRET", "WW_TEST_LOCAL_SETTING"} & set(os.environ)
    assert read_runtime_secret("WW_TEST_LOCAL_SECRET", max_bytes=16) == "primitive"
    assert runtime_value("WW_TEST_LOCAL_SETTING") == "1"
    assert runtime_value("WW_TEST_PRECEDENCE") == "process"
    # A value and its _FILE form stay ambiguous across process env and file.
    monkeypatch.setenv("WW_TEST_LOCAL_SECRET_FILE", str(tmp_path / "absent"))
    with pytest.raises(RuntimeSecretError):
        read_runtime_secret("WW_TEST_LOCAL_SECRET", max_bytes=16)


def test_initialized_verifier_never_rereads_local_env_per_request(
    tmp_path: Path,
) -> None:
    verifier = FirebaseVerifier(tmp_path)
    verifier.load_environment()
    # Simulate only the already-initialized SDK handle; no provider call occurs.
    verifier._app = object()
    (tmp_path / ".env").mkdir()  # Any later parse would now fail closed.
    verifier.ensure_ready()
    verifier._app = None


def test_browser_launch_environment_omits_backend_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from waterlooworks_flash_next.login import browser_environment

    for name in (
        "WW_CREDENTIALS_KEY",
        "WW_CREDENTIALS_KEY_FILE",
        "FIREBASE_SERVICE_ACCOUNT",
        "FIREBASE_SERVICE_ACCOUNT_FILE",
        "OPENAI_API_KEY",
        "WW_BOOTSTRAP_ADMIN_EMAIL",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "ANY_TOKEN",
    ):
        monkeypatch.setenv(name, "primitive")
    monkeypatch.setenv("HOME", "/home/test-user")
    environment = browser_environment()
    assert environment["HOME"] == "/home/test-user"
    assert "PATH" in environment
    assert not any(
        name.startswith(("WW_", "FIREBASE_", "OPENAI", "GOOGLE_"))
        or name.endswith("_FILE")
        or "TOKEN" in name
        for name in environment
    )
