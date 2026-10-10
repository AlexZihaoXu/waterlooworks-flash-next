"""Encrypted AI settings store; generated test secrets only, no provider calls."""

import json
import secrets
import sqlite3
import time
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr, ValidationError

from waterlooworks_flash_next.ai_settings import (
    AIConfig,
    AISettingsStore,
    CustomToken,
    OAuthCredential,
)
from waterlooworks_flash_next.credentials import CredentialError
from waterlooworks_flash_next.database import open_database
from waterlooworks_flash_next.openai_settings import OpenAIKey, OpenAISettingsStore


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AISettingsStore:
    monkeypatch.setenv("WW_CREDENTIALS_KEY", Fernet.generate_key().decode())
    path = tmp_path / "empty.sqlite3"
    open_database(path).close()
    return AISettingsStore(path)


def _oauth(**extra: object) -> OAuthCredential:
    return OAuthCredential.model_validate(
        {
            "type": "oauth",
            "access": secrets.token_urlsafe(32),
            "refresh": secrets.token_urlsafe(32),
            "expires": int(time.time() * 1000) + 3_600_000,
            **extra,
        }
    )


def test_draft_saves_without_activation_and_activation_is_explicit(
    store: AISettingsStore,
) -> None:
    assert store.read() == (AIConfig(), None, None)
    draft = AIConfig(model_id="gpt-5.6-luna", parallel_streams=3)
    store.save_draft(draft)
    assert store.read()[0] == draft
    assert store.active() is None
    # API-key mode cannot activate without a stored key.
    with pytest.raises(CredentialError):
        store.activate()
    OpenAISettingsStore(store.path).save(
        OpenAIKey(api_key=SecretStr(secrets.token_urlsafe(24)))
    )
    assert store.activate() == draft
    assert store.active() == draft
    view = store.view()
    assert view.active_label == "OpenAI API key / gpt-5.6-luna"
    store.save_draft(AIConfig())
    assert store.active() == draft  # saving a draft never switches extraction
    store.deactivate()
    assert store.active() is None


def test_device_id_is_stable_per_installation(store: AISettingsStore) -> None:
    first = store.device_id()
    assert len(first) == 36 and store.device_id() == first


def test_oauth_credentials_are_encrypted_purpose_bound_and_never_summarized(
    store: AISettingsStore,
) -> None:
    credential = _oauth(clientId="client-1", scopes=["chatgpt.tokens.use.direct"])
    store.save_oauth("chatgpt_signin", credential)
    raw = (
        sqlite3.connect(store.path)
        .execute("SELECT ciphertext,metadata_json FROM ai_credentials")
        .fetchone()
    )
    assert credential.access.encode() not in raw[0]
    assert credential.refresh.encode() not in raw[0]
    assert set(json.loads(raw[1])) == {"expires_at"}
    summary = store.view().model_dump_json()
    assert credential.access not in summary and credential.refresh not in summary
    assert store.view().credentials["chatgpt_signin"].configured is True
    assert store.load_slot("chatgpt_signin") == credential.public()
    # Ciphertext copied into another slot does not decrypt as that slot.
    connection = sqlite3.connect(store.path)
    with connection:
        connection.execute(
            "INSERT INTO ai_credentials VALUES('codex_device',?,'{}',"
            "'2026-01-01T00:00:00+00:00')",
            (raw[0],),
        )
    connection.close()
    with pytest.raises(CredentialError):
        store.load_slot("codex_device")
    assert store.view().credentials["codex_device"].configured is True  # MAC only
    store.delete("chatgpt_signin")
    assert store.load_slot("chatgpt_signin") is None


def test_sidecar_credentials_resolve_only_for_the_active_provider(
    store: AISettingsStore,
) -> None:
    codex = _oauth(accountId="acct")
    store.save_oauth("codex_device", codex)
    config = AIConfig(access_mode="codex_device", model_id="gpt-5.6-luna")
    assert store.credential_for_provider(config, "openai") is None
    assert store.credential_for_provider(config, "openai-codex") == codex.public()
    lock = store.lock(config)
    try:
        rotated = _oauth(accountId="acct")
        store.commit_locked(config, rotated)
    finally:
        lock.release()
    assert store.credential_for_provider(config, "openai-codex") == rotated.public()
    with pytest.raises(CredentialError):
        store.commit_locked(config, _oauth())  # Codex credentials need accountId
    custom = AIConfig(
        access_mode="custom",
        model_id="m",
        custom_base_url="http://100.64.0.1:8000/v1",
        custom_context_window=32768,
    )
    assert store.credential_for_provider(custom, "custom") is None
    token = secrets.token_urlsafe(24)
    store.save_custom_token(CustomToken(token=SecretStr(token)))
    assert store.credential_for_provider(custom, "custom") == {
        "type": "api_key",
        "key": token,
    }
    with pytest.raises(CredentialError):
        store.lock(custom)  # API-key style credentials are never rotated


def test_validation_errors_never_echo_input_values() -> None:
    secret = secrets.token_urlsafe(24)
    for payload in (
        {"access_mode": "openai_api_key", "unexpected": secret},
        {"model_id": f"bad model {secret}"},
        {"custom_reasoning_field": "messages"},
        {"access_mode": "custom", "custom_disable_with": {"model": secret}},
        {"parallel_streams": 9},
    ):
        with pytest.raises(ValidationError) as error:
            AIConfig.model_validate(payload)
        assert secret not in str(error.value)
    with pytest.raises(ValidationError) as error:
        CustomToken.model_validate({"token": "has space " + secret})
    assert secret not in str(error.value)
    with pytest.raises(ValidationError) as error:
        OAuthCredential.model_validate(
            {"type": "oauth", "access": secret, "refresh": 1, "expires": 1}
        )
    assert secret not in str(error.value)
