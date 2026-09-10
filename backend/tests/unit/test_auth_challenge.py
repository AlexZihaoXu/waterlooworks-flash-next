"""Clock/channel primitives only: no Duo DOM or live authentication."""

import secrets
from pathlib import Path

import pytest

from waterlooworks_flash_next.auth_challenge import AuthChallenge
from waterlooworks_flash_next.login import (
    AuthDeadline,
    LoginFlowError,
    browser_settings,
)


def test_deadline_cannot_be_extended_by_repeated_challenge() -> None:
    clock = [10.0]
    deadline = AuthDeadline(seconds=5, clock=lambda: clock[0])
    channel = AuthChallenge(clock=lambda: clock[0])
    channel.begin("run", deadline)
    code = f"{secrets.randbelow(1000):03d}"
    channel.publish("run", code)
    assert channel.snapshot("run").code == code
    assert code not in repr(channel.snapshot("run"))
    clock[0] = 14
    channel.publish("run", code)
    assert channel.snapshot("run").expires_in_seconds == 1
    clock[0] = 15
    assert channel.snapshot("run").code is None
    with pytest.raises(LoginFlowError, match="deadline"):
        deadline.remaining_ms()


def test_channel_fences_old_runs_and_clears_terminal() -> None:
    channel = AuthChallenge()
    channel.begin("new", AuthDeadline())
    code = f"{secrets.randbelow(1000):03d}"
    channel.publish("old", code)
    assert channel.snapshot("new").code is None
    channel.publish("new", code)
    assert channel.snapshot("old").code is None
    channel.clear("old")
    assert channel.snapshot("new").code == code
    channel.clear("new")
    assert channel.snapshot("new").code is None
    channel.publish("new", code)
    assert channel.snapshot("new").code is None


def test_browser_defaults_headless_and_requires_private_profile_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WW_BROWSER_PROFILE", raising=False)
    monkeypatch.delenv("WW_BROWSER_HEADLESS", raising=False)
    with pytest.raises(LoginFlowError):
        browser_settings()
    monkeypatch.setenv("WW_BROWSER_PROFILE", str(tmp_path / "profile"))
    assert browser_settings().headless is True
    monkeypatch.setenv("WW_BROWSER_HEADLESS", "0")
    assert browser_settings().headless is False
    monkeypatch.setenv("WW_BROWSER_HEADLESS", "invalid")
    with pytest.raises(LoginFlowError):
        browser_settings()


def test_submission_gate_is_once_even_when_dispatch_is_ambiguous() -> None:
    from typing import cast

    from playwright.sync_api import BrowserContext, Page, Playwright

    from waterlooworks_flash_next.login import LoginSession

    # Opaque handles: this gate performs no browser actions or DOM simulation.
    session = LoginSession(
        cast(Playwright, object()),
        cast(BrowserContext, object()),
        cast(Page, object()),
        AuthDeadline(),
    )
    session.claim_submission()
    with pytest.raises(LoginFlowError, match="no retry"):
        session.claim_submission()


def test_safe_duo_failure_closes_without_process_exit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from typing import cast

    from waterlooworks_flash_next.duo import stop_login
    from waterlooworks_flash_next.login import LoginSession

    closed = []

    class CleanupCounter:
        def close(self) -> None:
            closed.append(True)

    from waterlooworks_flash_next.logging import stage_logger

    logger = stage_logger("duo")
    logger.addHandler(caplog.handler)
    try:
        with pytest.raises(LoginFlowError, match="stopped"):
            stop_login(cast(LoginSession, CleanupCounter()), "Authentication stopped.")
        assert closed == [True]
        assert "no automatic retry" in caplog.text
    finally:
        logger.removeHandler(caplog.handler)


@pytest.mark.parametrize(
    "url",
    [
        "https://waterlooworks.uwaterloo.ca:444/",
        "https://user@waterlooworks.uwaterloo.ca/",
        "https://waterlooworks.uwaterloo.ca:invalid/",
        "http://waterlooworks.uwaterloo.ca/",
        "https://waterlooworks.uwaterloo.ca.invalid/",
    ],
)
def test_authenticated_destination_rejects_nonexact_origin(url: str) -> None:
    from waterlooworks_flash_next.duo import authenticated_destination

    assert not authenticated_destination(url, True, True)


def test_refresh_auth_failure_finalizes_and_releases_provider_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from waterlooworks_flash_next import refresh
    from waterlooworks_flash_next.api_models import RefreshSnapshot
    from waterlooworks_flash_next.database import open_database
    from waterlooworks_flash_next.provider_lease import ProviderLease

    path = tmp_path / "empty.sqlite3"
    run_id = refresh.claim_refresh(path)
    service = refresh.RefreshService(path, tmp_path)
    service.state = RefreshSnapshot(
        run_id=run_id, status="running", stage="authentication"
    )
    service.provider_lease = ProviderLease(path)
    service.provider_lease.acquire()

    def fail_login(*args: object, **kwargs: object) -> None:
        raise LoginFlowError("Authentication stopped.")

    monkeypatch.setattr(refresh, "start_login", fail_login)
    service._run(run_id)
    assert service.snapshot().status == "failed"
    assert service.challenge_status().code is None
    assert service.provider_lease is None
    with ProviderLease(path):
        pass
    connection = open_database(path)
    try:
        assert (
            connection.execute("SELECT status FROM collection_runs").fetchone()[0]
            == "failed"
        )
        assert (
            connection.execute("SELECT count(*) FROM openai_attempts").fetchone()[0]
            == 0
        )
    finally:
        connection.close()
