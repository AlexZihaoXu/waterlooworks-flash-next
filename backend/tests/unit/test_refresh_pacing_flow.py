"""Shared-gate integration: caps, ban-risk signals, one sign-in, startup config.

Uses real empty migrations, empty control runs, primitive operations and
identity-only sentinels. No posting records, markup or platform responses are
fabricated and nothing contacts WaterlooWorks.
"""

import sqlite3
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from waterlooworks_flash_next import detail_fetch, refresh
from waterlooworks_flash_next.database import StoredListing, open_database
from waterlooworks_flash_next.detail_fetch import (
    DetailCollection,
    DetailFetchError,
    DetailSource,
)
from waterlooworks_flash_next.listings import FailureCategory
from waterlooworks_flash_next.refresh import RefreshService, claim_refresh
from waterlooworks_flash_next.ww_pacing import (
    UNHAPPY_BLOCK_SECONDS,
    PacingConfig,
    PacingConfigError,
    RequestBudgetExhausted,
    RequestGate,
)


def control_gate(cap: int = 400, daily: int = 10_000) -> RequestGate:
    return RequestGate(
        PacingConfig(cap, 1200, 30), daily_remaining=daily, sleep=lambda s: None
    )


class Attempts:
    """Records storage transitions in place of content checkpoints."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.events: list[tuple[str, str]] = []
        self.blocked: list[int] = []
        for name in (
            "start_content_attempt",
            "complete_content_attempt",
            "fail_content_attempt",
            "interrupt_content_attempt",
            "record_content_request",
        ):
            monkeypatch.setattr(detail_fetch, name, self._recorder(name))
        monkeypatch.setattr(
            detail_fetch,
            "record_blocked_until",
            lambda connection, run, seconds: self.blocked.append(seconds),
        )

    def _recorder(self, name: str) -> Callable[..., str]:
        def record(*args: object, **kwargs: object) -> str:
            identity = str(args[2]) if name == "start_content_attempt" else ""
            self.events.append((name, identity))
            return identity

        return record


def sentinels(count: int) -> list[StoredListing]:
    return [
        cast(
            StoredListing,
            SimpleNamespace(id=f"control-{n}", title="control", organization=None),
        )
        for n in range(count)
    ]


def test_cap_stops_before_starting_a_posting_that_cannot_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = Attempts(monkeypatch)
    gate = control_gate(cap=1)
    collection = DetailCollection(
        None,
        cast(sqlite3.Connection, None),
        "control-run",
        sentinels(2),
        lambda progress: None,
        bootstrap=lambda posting, request: pytest.fail("bootstrap needs 2 requests"),
        gate=gate,
    )
    with pytest.raises(RequestBudgetExhausted) as stop:
        collection.run()
    assert stop.value.reason == "request_cap"
    assert attempts.events == [] and gate.requests == 0


def test_cap_reached_mid_posting_closes_the_attempt_as_interrupted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = Attempts(monkeypatch)
    gate = control_gate(cap=1)
    source = DetailSource(((("action", "control"),),) * 2, {}, (), lambda: True)
    collection = DetailCollection(
        source,
        cast(sqlite3.Connection, None),
        "control-run",
        sentinels(1),
        lambda progress: None,
        gate=gate,
    )
    collection._needed = lambda: 1  # type: ignore[method-assign]
    # Primitive transport: the first template returns the observed metadata
    # shape only by identity, so the next template would need a second request.
    monkeypatch.setattr(
        source,
        "fetch_once",
        lambda template, posting: (
            '{"id": "control-0", "position": 0, "org": 0, "div": 0, "divId": 0}'
        ),
    )
    with pytest.raises(RequestBudgetExhausted):
        collection.run()
    names = [name for name, _ in attempts.events]
    assert names[0] == "start_content_attempt"
    assert names[-1] == "interrupt_content_attempt"
    assert "fail_content_attempt" not in names
    assert gate.requests == 1


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("We detected unusual activity. Please complete the CAPTCHA.", "rate_limit"),
        ("Your session has expired. Please log in.", "authentication"),
    ],
)
def test_unexpected_challenge_or_sign_in_page_stops_and_blocks(
    monkeypatch: pytest.MonkeyPatch, text: str, category: str
) -> None:
    attempts = Attempts(monkeypatch)
    source = DetailSource(((("action", "control"),),), {}, (), lambda: True)
    source.markup_index = 0
    monkeypatch.setattr(source, "fetch_once", lambda template, posting: text)
    collection = DetailCollection(
        source,
        cast(sqlite3.Connection, None),
        "control-run",
        sentinels(3),
        lambda progress: None,
        gate=control_gate(),
    )
    with pytest.raises(DetailFetchError) as stopped:
        collection.run()
    assert stopped.value.category.value == category
    assert attempts.blocked == [UNHAPPY_BLOCK_SECONDS]
    # Not treated as a skippable per-posting validation failure.
    assert [n for n, _ in attempts.events].count("start_content_attempt") == 1


def test_rate_limit_inside_details_is_never_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = Attempts(monkeypatch)
    source = DetailSource(((("action", "control"),),), {}, (), lambda: True)
    source.markup_index = 0
    calls: list[int] = []

    def limited(template: int, posting: str) -> str:
        calls.append(1)
        raise DetailFetchError(FailureCategory.RATE_LIMIT, status=429, retry_after=5)

    monkeypatch.setattr(source, "fetch_once", limited)
    collection = DetailCollection(
        source,
        cast(sqlite3.Connection, None),
        "control-run",
        sentinels(2),
        lambda progress: None,
        gate=control_gate(),
    )
    with pytest.raises(DetailFetchError):
        collection.run()
    assert calls == [1]
    assert attempts.blocked == [3600]


def test_a_failed_sign_in_ends_the_run_after_exactly_one_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from waterlooworks_flash_next.login import LoginFlowError

    path = tmp_path / "db.sqlite3"
    run_id = claim_refresh(path)
    logins: list[int] = []

    def failing_login(*args: object, **kwargs: object) -> None:
        logins.append(1)
        raise LoginFlowError("Login rehearsal stopped.")

    monkeypatch.setattr(refresh, "start_login", failing_login)
    monkeypatch.setattr(
        refresh,
        "submit_and_observe_duo",
        lambda *args, **kwargs: pytest.fail("no Duo after a failed sign-in"),
    )
    service = RefreshService(path, tmp_path)
    service._run(run_id)
    assert logins == [1]
    snapshot = service.snapshot()
    assert snapshot.status == "failed"
    assert snapshot.error_category == "internal"
    # The sign-in page loads still count against the rolling budget.
    assert snapshot.ww_requests == refresh.LOGIN_BROWSER_REQUESTS
    connection = open_database(path)
    try:
        assert connection.execute(
            "SELECT ww_requests FROM collection_run_pacing WHERE run_id=?", (run_id,)
        ).fetchone()[0] == (refresh.LOGIN_BROWSER_REQUESTS)
    finally:
        connection.close()
    # The next start honours the interval; nothing retries the login.
    with pytest.raises(refresh.RefreshConflict) as refusal:
        claim_refresh(path)
    assert refusal.value.recent_finished_at is not None


def test_run_finish_records_the_pacing_stop_and_restores_it(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    run_id = claim_refresh(path)
    service = RefreshService(path, None)
    service.state = service.state.model_copy(
        update={"run_id": run_id, "status": "running", "pacing_stop": "request_cap"}
    )
    service._finish(run_id, True, None)
    assert service.snapshot().status == "completed"
    assert any("finished partially" in log.message for log in service.state.logs)
    restored = RefreshService(path, None)
    restored.restore()
    assert restored.snapshot().pacing_stop == "request_cap"
    assert restored.snapshot().status == "completed"
    # The confirmable interval is no member-visible countdown.
    assert restored.snapshot().retry_after == 0
    assert restored.pacing_status().cooldown_until is not None


def test_invalid_pacing_environment_fails_app_startup_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from waterlooworks_flash_next.api import create_app

    monkeypatch.setenv("WW_DATABASE", str(tmp_path / "db.sqlite3"))
    monkeypatch.setenv("WW_REFRESH_DAILY_REQUEST_CAP", "unlimited")
    with pytest.raises(PacingConfigError) as error:
        create_app()
    assert str(error.value) == (
        "WW_REFRESH_DAILY_REQUEST_CAP must be an integer from 1 to 5000."
    )


def test_admin_pacing_status_reports_usage_block_and_last_stop(
    tmp_path: Path,
) -> None:
    from datetime import UTC, datetime

    from waterlooworks_flash_next.detail_fetch import record_blocked_until
    from waterlooworks_flash_next.refresh_budget import record_ww_request

    path = tmp_path / "db.sqlite3"
    run_id = claim_refresh(path)
    connection = open_database(path)
    try:
        for _ in range(7):
            record_ww_request(connection, run_id)
        record_blocked_until(connection, run_id, UNHAPPY_BLOCK_SECONDS)
    finally:
        connection.close()
    service = RefreshService(path, None, pacing=PacingConfig(50, 100, 30))
    service.state = service.state.model_copy(
        update={"run_id": run_id, "status": "running", "pacing_stop": "ban_risk"}
    )
    service._finish(run_id, False, "rate_limit")
    status = service.pacing_status()
    assert (status.request_cap, status.daily_request_cap) == (50, 100)
    assert (status.used_24h, status.remaining_24h) == (7, 93)
    assert status.last_pacing_stop == "ban_risk"
    assert status.last_error_category == "rate_limit"
    assert status.blocked_until is not None
    assert status.next_allowed_at == status.blocked_until
    blocked = datetime.fromisoformat(status.blocked_until) - datetime.now(UTC)
    assert 6 * 3600 - 30 <= blocked.total_seconds() <= 6 * 3600
    assert service.admin_status().pacing == status
