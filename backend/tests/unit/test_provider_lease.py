"""Temporary empty-file ownership primitives; no browsers, providers or private data."""

from pathlib import Path

import pytest

from waterlooworks_flash_next.provider_lease import ProviderBusy, ProviderLease


def test_provider_lease_contention_release_and_explicit_handoff(tmp_path: Path) -> None:
    path = tmp_path / "empty.sqlite3"
    path.touch()
    with ProviderLease(path) as owner:
        owner.require_owner(path)
        with pytest.raises(ProviderBusy):
            ProviderLease(path).acquire()
        other = tmp_path / "different.sqlite3"
        other.touch()
        with pytest.raises(ProviderBusy):
            owner.require_owner(other)
    with pytest.raises(ProviderBusy):
        owner.require_owner(path)
    with ProviderLease(path) as replacement:
        replacement.require_owner(path)
    assert Path(str(path) + ".provider.lock").exists()


def test_provider_lease_failed_scope_releases_ownership(tmp_path: Path) -> None:
    path = tmp_path / "empty.sqlite3"
    path.touch()
    with pytest.raises(RuntimeError), ProviderLease(path):
        raise RuntimeError("primitive failure")
    with ProviderLease(path) as owner:
        owner.require_owner(path)


def test_refresh_busy_admission_has_no_claim_or_cooldown_and_releases_failed_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import secrets

    from cryptography.fernet import Fernet
    from pydantic import SecretStr

    from waterlooworks_flash_next.credentials import CredentialPair, CredentialStore
    from waterlooworks_flash_next.database import open_database, start_collection_run
    from waterlooworks_flash_next.openai_settings import OpenAIKey, OpenAISettingsStore
    from waterlooworks_flash_next.refresh import RefreshConflict, RefreshService

    path = tmp_path / "empty.sqlite3"
    connection = open_database(path)
    monkeypatch.setenv("WW_CREDENTIALS_KEY", Fernet.generate_key().decode())
    CredentialStore(path).save(
        CredentialPair(
            username=SecretStr(secrets.token_urlsafe()),
            password=SecretStr(secrets.token_urlsafe()),
        )
    )
    OpenAISettingsStore(path).save(
        OpenAIKey(api_key=SecretStr(secrets.token_urlsafe()))
    )
    service = RefreshService(path, tmp_path)
    with ProviderLease(path):
        with pytest.raises(RefreshConflict) as busy:
            service.start()
        assert busy.value.status_code == 409
        assert (
            connection.execute("SELECT count(*) FROM collection_runs").fetchone()[0]
            == 0
        )
    start_collection_run(connection)
    with pytest.raises(RefreshConflict):
        service.start()
    with ProviderLease(path) as lease:
        lease.require_owner(path)
    assert service.cooldown_until is None
    connection.close()
