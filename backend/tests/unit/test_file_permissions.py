"""Owner/mode enforcement on key files, env provisioning, databases and profiles."""

import os
import re
import sqlite3
import stat
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from waterlooworks_flash_next import login
from waterlooworks_flash_next.credentials import CredentialError, initialize_key
from waterlooworks_flash_next.database import DatabaseError, open_database
from waterlooworks_flash_next.runtime_secrets import (
    RuntimeSecretError,
    read_runtime_secret,
)

posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX modes and owners")


def foreign_uid(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    owner = path.stat().st_uid
    monkeypatch.setattr(os, "geteuid", lambda: owner + 1)


@posix_only
@pytest.mark.parametrize("mode", [0o640, 0o604, 0o644, 0o660, 0o666])
def test_runtime_secret_file_must_not_be_group_or_other_accessible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: int
) -> None:
    path = tmp_path / "secret"
    path.write_bytes(b"primitive\n")
    monkeypatch.setenv("WW_TEST_RUNTIME_SECRET_FILE", str(path))
    for allowed in (0o400, 0o600):
        path.chmod(allowed)
        assert (
            read_runtime_secret("WW_TEST_RUNTIME_SECRET", max_bytes=16) == "primitive"
        )
    path.chmod(mode)
    with pytest.raises(RuntimeSecretError, match="Runtime secret unavailable") as error:
        read_runtime_secret("WW_TEST_RUNTIME_SECRET", max_bytes=16)
    assert str(path) not in str(error.value)


@posix_only
def test_runtime_secret_file_must_belong_to_the_running_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "secret"
    path.write_bytes(b"primitive\n")
    path.chmod(0o400)
    monkeypatch.setenv("WW_TEST_RUNTIME_SECRET_FILE", str(path))
    foreign_uid(monkeypatch, path)
    with pytest.raises(RuntimeSecretError, match="Runtime secret unavailable"):
        read_runtime_secret("WW_TEST_RUNTIME_SECRET", max_bytes=16)


def prepared_database(tmp_path: Path) -> Path:
    path = tmp_path / "db.sqlite3"
    open_database(path).close()
    return path


@posix_only
def test_initialize_key_refuses_loose_env_file_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WW_CREDENTIALS_KEY", raising=False)
    monkeypatch.delenv("WW_CREDENTIALS_KEY_FILE", raising=False)
    database = prepared_database(tmp_path)
    env = tmp_path / ".env"
    env.write_text("UNRELATED_SETTING=1\n")
    env.chmod(0o644)
    with pytest.raises(CredentialError, match="private"):
        initialize_key(env, database)
    assert env.read_text() == "UNRELATED_SETTING=1\n"
    assert stat.S_IMODE(env.stat().st_mode) == 0o644
    env.chmod(0o600)
    initialize_key(env, database)
    assert "WW_CREDENTIALS_KEY=" in env.read_text()
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


@posix_only
def test_initialize_key_creates_new_env_file_owner_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WW_CREDENTIALS_KEY", raising=False)
    monkeypatch.delenv("WW_CREDENTIALS_KEY_FILE", raising=False)
    database = prepared_database(tmp_path)
    env = tmp_path / ".env"
    previous = os.umask(0)
    try:
        initialize_key(env, database)
    finally:
        os.umask(previous)
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


@posix_only
def test_initialize_key_refuses_symlink_and_hardlink_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WW_CREDENTIALS_KEY", raising=False)
    monkeypatch.delenv("WW_CREDENTIALS_KEY_FILE", raising=False)
    database = prepared_database(tmp_path)
    target = tmp_path / "elsewhere"
    target.write_text("")
    target.chmod(0o600)
    link = tmp_path / "link.env"
    link.symlink_to(target)
    with pytest.raises(CredentialError):
        initialize_key(link, database)
    alias = tmp_path / "alias.env"
    os.link(target, alias)
    with pytest.raises(CredentialError):
        initialize_key(alias, database)
    assert target.read_text() == ""


@posix_only
def test_initialize_key_does_not_follow_a_symlink_swapped_in_after_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WW_CREDENTIALS_KEY", raising=False)
    monkeypatch.delenv("WW_CREDENTIALS_KEY_FILE", raising=False)
    database = prepared_database(tmp_path)
    env = tmp_path / ".env"
    target = tmp_path / "attacker"
    target.write_text("")
    target.chmod(0o600)
    real_is_symlink = Path.is_symlink

    def swap_after_check(self: Path) -> bool:
        result = real_is_symlink(self)
        if self == env and not result and not os.path.lexists(env):
            env.symlink_to(target)
        return result

    monkeypatch.setattr(Path, "is_symlink", swap_after_check)
    with pytest.raises(CredentialError):
        initialize_key(env, database)
    assert target.read_text() == ""


def test_stale_initialization_lock_has_a_distinct_actionable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WW_CREDENTIALS_KEY", raising=False)
    monkeypatch.delenv("WW_CREDENTIALS_KEY_FILE", raising=False)
    database = prepared_database(tmp_path)
    env = tmp_path / ".env"
    lock = tmp_path / ".env.credentials-key.lock"
    lock.write_bytes(b"")
    with pytest.raises(CredentialError, match="lock") as error:
        initialize_key(env, database)
    assert lock.name in str(error.value)
    assert str(tmp_path) not in str(error.value)
    assert lock.exists() and not env.exists()
    lock.unlink()
    initialize_key(env, database)
    assert not lock.exists()
    Fernet(env.read_text().split("=", 1)[1].strip().encode())


@posix_only
def test_existing_database_must_be_private_and_owned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = prepared_database(tmp_path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    open_database(path).close()
    for mode in (0o644, 0o640, 0o604):
        path.chmod(mode)
        with pytest.raises(DatabaseError, match="permissions") as error:
            open_database(path)
        assert str(path) not in str(error.value)
    path.chmod(0o600)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] > 1
    foreign_uid(monkeypatch, path)
    with pytest.raises(DatabaseError, match="permissions"):
        open_database(path)


@posix_only
def test_profile_directory_is_created_or_tightened_to_owner_only(
    tmp_path: Path,
) -> None:
    fresh = tmp_path / "nested" / "profile"
    login.secure_profile_directory(fresh)
    assert stat.S_IMODE(fresh.stat().st_mode) == 0o700
    loose = tmp_path / "loose"
    loose.mkdir()
    loose.chmod(0o755)
    login.secure_profile_directory(loose)
    assert stat.S_IMODE(loose.stat().st_mode) == 0o700


@posix_only
def test_profile_directory_refuses_symlinks_files_and_foreign_owners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(login.LoginFlowError):
        login.secure_profile_directory(link)
    plain = tmp_path / "file"
    plain.write_text("")
    with pytest.raises(login.LoginFlowError):
        login.secure_profile_directory(plain)
    foreign_uid(monkeypatch, real)
    with pytest.raises(login.LoginFlowError):
        login.secure_profile_directory(real)


class RecordingContext:
    def __init__(self, fail_clear: bool) -> None:
        self.events: list[str] = []
        self.fail_clear = fail_clear

    def clear_cookies(self, **filters: object) -> None:
        self.events.append("clear_cookies")
        self.filters = filters
        if self.fail_clear:
            raise RuntimeError("diagnostics must not escape")

    def close(self) -> None:
        self.events.append("close")


class RecordingPlaywright:
    stopped = False

    def stop(self) -> None:
        self.stopped = True


@pytest.mark.parametrize("fail_clear", [False, True])
def test_session_close_clears_cookies_before_closing_even_if_clearing_fails(
    fail_clear: bool,
) -> None:
    context = RecordingContext(fail_clear)
    playwright = RecordingPlaywright()
    session = login.LoginSession(
        playwright,  # type: ignore[arg-type]
        context,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        login.AuthDeadline(),
    )
    session.close()
    assert context.events == ["clear_cookies", "close"]
    assert playwright.stopped
    # Session cookies go; only Duo's remembered-device state survives.
    domain = context.filters["domain"]
    assert isinstance(domain, re.Pattern)
    for cleared in (
        "waterlooworks.uwaterloo.ca",
        ".uwaterloo.ca",
        "adfs.uwaterloo.ca",
        "duosecurity.com.example",
        "notduosecurity.com",
    ):
        assert domain.search(cleared)
    for kept in ("api-1a2b3c4d.duosecurity.com", ".duosecurity.com", "duosecurity.com"):
        assert not domain.search(kept)
    session.close()
    assert context.events == ["clear_cookies", "close"]
