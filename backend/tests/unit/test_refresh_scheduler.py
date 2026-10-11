"""Scheduler decisions on empty SQLite with an injected clock and no real sleeps.

The service double only records start calls (it never collects anything); the
real RefreshService is used where its own guards decide the outcome. Nothing
contacts WaterlooWorks.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from waterlooworks_flash_next import refresh
from waterlooworks_flash_next.api_models import RefreshSnapshot
from waterlooworks_flash_next.credentials import CredentialError
from waterlooworks_flash_next.database import open_database, start_collection_run
from waterlooworks_flash_next.listings import FailureCategory, ListingFetchError
from waterlooworks_flash_next.provider_lease import ProviderLease
from waterlooworks_flash_next.refresh import RefreshConflict, RefreshService, RunOutcome
from waterlooworks_flash_next.refresh_schedule import (
    RefreshScheduler,
    ScheduleStore,
    scheduled_failure,
)


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class Service:
    """Records scheduler calls; ``outcome`` decides what start() does."""

    def __init__(self, path: Path, outcome: object = "run") -> None:
        self.path = path
        self.outcome = outcome
        self.starts = 0
        self.messages: list[str] = []
        self.notice: tuple[str | None, str | None] = (None, None)

    def start(self) -> str:
        self.starts += 1
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return refresh.claim_refresh(self.path)

    def emit(self, message: str, level: str = "info") -> None:
        self.messages.append(message)

    def set_schedule_notice(self, next_at: str | None, failed_at: str | None) -> None:
        self.notice = (next_at, failed_at)


def scheduler(
    tmp_path: Path, outcome: object = "run", *, enable: bool = True
) -> tuple[RefreshScheduler, Service, Clock, ScheduleStore]:
    path = tmp_path / "empty.sqlite3"
    store = ScheduleStore(path)
    store.add(450, 30)  # 07:30 Toronto = 11:30 UTC in July
    if enable:
        store.set_enabled(True, utc("2026-07-01 10:00"))
    service = Service(path, outcome)
    clock = Clock(utc("2026-07-01 11:30:05"))
    return (
        RefreshScheduler(store, service, active=True, clock=clock),
        service,
        clock,
        store,
    )


def test_due_slot_starts_once_and_is_never_retried(tmp_path: Path) -> None:
    instance, service, clock, store = scheduler(tmp_path)
    instance.tick()
    assert service.starts == 1
    [entry] = store.recent()
    assert entry.outcome == "started" and entry.run_id is not None
    assert any("Scheduled refresh" in message for message in service.messages)
    clock.now += timedelta(seconds=30)
    instance.tick()
    assert service.starts == 1


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (RefreshConflict(409), "skipped_running"),
        (RefreshConflict(429, 60, "Refresh cooldown is active."), "skipped_blocked"),
        (CredentialError("AI extraction is not activated."), "failed_to_start"),
        (RuntimeError("Manual refresh is disabled."), "skipped_disabled"),
        (ValueError("unexpected"), "failed_to_start"),
    ],
)
def test_start_refusals_map_to_skips_without_retry(
    tmp_path: Path, error: Exception, outcome: str
) -> None:
    instance, service, clock, store = scheduler(tmp_path, error)
    instance.tick()
    clock.now += timedelta(minutes=1)
    instance.tick()
    assert service.starts == 1
    [entry] = store.recent()
    assert (entry.outcome, entry.run_id) == (outcome, None)


def test_missed_slots_outside_grace_window_never_catch_up(tmp_path: Path) -> None:
    instance, service, clock, store = scheduler(tmp_path)
    clock.now = utc("2026-07-01 11:40:00")
    instance.tick()
    assert service.starts == 0 and store.recent() == []


def test_disabled_toggle_or_inactive_server_never_fires(tmp_path: Path) -> None:
    instance, service, _, store = scheduler(tmp_path, enable=False)
    instance.tick()
    assert service.starts == 0 and store.recent() == []
    store.set_enabled(True, utc("2026-07-01 10:00"))
    inactive = RefreshScheduler(
        store, service, active=False, clock=Clock(utc("2026-07-01 11:30:05"))
    )
    inactive.tick()
    assert service.starts == 0


def test_reenabling_after_a_slot_came_due_does_not_fire_it(tmp_path: Path) -> None:
    instance, service, clock, store = scheduler(tmp_path)
    store.set_enabled(False, utc("2026-07-01 11:20"))
    store.set_enabled(True, utc("2026-07-01 11:31"))
    clock.now = utc("2026-07-01 11:32")
    instance.tick()
    assert service.starts == 0


def test_two_workers_on_one_database_fire_a_slot_once(tmp_path: Path) -> None:
    first, service, clock, store = scheduler(tmp_path)
    second = RefreshScheduler(
        ScheduleStore(store.path), service, active=True, clock=clock
    )
    first.tick()
    second.tick()
    assert service.starts == 1


def test_publish_exposes_next_run_only_when_active_and_enabled(tmp_path: Path) -> None:
    instance, service, clock, store = scheduler(tmp_path)
    clock.now = utc("2026-07-01 12:00")
    instance.publish()
    assert service.notice == ("2026-07-02T11:30:00+00:00", None)
    store.set_enabled(False, clock.now)
    instance.publish()
    assert service.notice[0] is None
    view = instance.view()
    assert view.server_enabled is True and view.enabled is False
    assert view.times == ["07:30"] and view.next_run_at is None


def outcome(run_id: str, **overrides: object) -> RunOutcome:
    values: dict[str, object] = {
        "run_id": run_id,
        "status": "failed",
        "error_category": "internal",
        "pacing_stop": None,
        "authenticated": True,
    }
    values.update(overrides)
    return RunOutcome(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("overrides", "category"),
    [
        ({"authenticated": False}, "sign_in"),
        ({"authenticated": False, "error_category": None}, "sign_in"),
        ({"error_category": "authentication"}, "authentication"),
        ({"error_category": "rate_limit"}, "rate_limit"),
        ({"error_category": "redirect"}, "redirect"),
        ({"pacing_stop": "ban_risk", "error_category": "http_status"}, "ban_risk"),
        ({"pacing_stop": "ban_risk", "authenticated": False}, "ban_risk"),
        ({"error_category": "network"}, None),
        ({"error_category": "timeout"}, None),
        ({"error_category": "internal"}, None),
        ({"status": "interrupted", "error_category": None}, None),
        ({"status": "completed", "error_category": None}, None),
        (
            {"status": "completed", "error_category": None, "pacing_stop": "daily_cap"},
            None,
        ),
    ],
)
def test_failure_category_mapping(
    overrides: dict[str, object], category: str | None
) -> None:
    assert scheduled_failure(outcome("run", **overrides)) == category


def test_bad_scheduled_failure_turns_scheduling_off_and_publishes(
    tmp_path: Path,
) -> None:
    instance, service, clock, store = scheduler(tmp_path)
    instance.tick()
    [entry] = store.recent()
    assert entry.run_id is not None
    instance.run_finished(outcome(entry.run_id, error_category="rate_limit"))
    state = store.state()
    assert state.enabled is False
    assert state.failure is not None
    assert (state.failure.run_id, state.failure.slot, state.failure.category) == (
        entry.run_id,
        "2026-07-01 07:30",
        "rate_limit",
    )
    assert state.failure.failed_at == utc("2026-07-01 11:30")
    assert store.recent()[0].failure_category == "rate_limit"
    assert service.notice == (None, "2026-07-01T11:30:00+00:00")
    # Turning it back on clears the banner but keeps the slot history.
    store.set_enabled(True, clock.now)
    assert store.state().failure is None
    assert store.recent()[0].failure_category == "rate_limit"


def test_ordinary_failures_and_manual_runs_keep_scheduling_on(tmp_path: Path) -> None:
    instance, _, _, store = scheduler(tmp_path)
    instance.tick()
    [entry] = store.recent()
    assert entry.run_id is not None
    instance.run_finished(outcome(entry.run_id, error_category="network"))
    assert store.state().enabled is True
    connection = open_database(store.path)
    try:
        manual = start_collection_run(connection)
    finally:
        connection.close()
    instance.run_finished(outcome(manual, authenticated=False))
    assert store.state().enabled is True


def test_async_loop_ticks_until_cancelled_without_real_sleeps(tmp_path: Path) -> None:
    instance, service, _, _ = scheduler(tmp_path)
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 2:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(instance.run(sleep=fake_sleep))
    assert waits == [30, 30]
    assert service.starts == 1


def test_real_service_disabled_root_maps_to_skipped_disabled(tmp_path: Path) -> None:
    path = tmp_path / "empty.sqlite3"
    store = ScheduleStore(path)
    store.add(450, 30)
    store.set_enabled(True, utc("2026-07-01 10:00"))
    service = RefreshService(path, None)
    RefreshScheduler(
        store, service, active=True, clock=Clock(utc("2026-07-01 11:30:05"))
    ).tick()
    assert store.recent()[0].outcome == "skipped_disabled"


def _service_in_run(tmp_path: Path) -> tuple[RefreshService, str, list[RunOutcome]]:
    path = tmp_path / "empty.sqlite3"
    run_id = refresh.claim_refresh(path)
    service = RefreshService(path, tmp_path)
    service.state = RefreshSnapshot(
        run_id=run_id, status="running", stage="authentication"
    )
    service.provider_lease = ProviderLease(path)
    service.provider_lease.acquire()
    seen: list[RunOutcome] = []
    service.on_finished = seen.append
    return service, run_id, seen


def test_run_reports_sign_in_failures_as_unauthenticated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, run_id, seen = _service_in_run(tmp_path)

    def fail_login(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Authentication stopped.")

    monkeypatch.setattr(refresh, "start_login", fail_login)
    service._run(run_id)
    assert seen == [
        RunOutcome(
            run_id=run_id,
            status="failed",
            error_category="internal",
            pacing_stop=None,
            authenticated=False,
        )
    ]


def test_run_reports_mid_run_rate_limit_as_ban_risk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, run_id, seen = _service_in_run(tmp_path)
    session = SimpleNamespace(page=None, close=lambda: None)
    monkeypatch.setattr(refresh, "start_login", lambda *a, **k: session)
    monkeypatch.setattr(refresh, "submit_and_observe_duo", lambda *a, **k: None)
    monkeypatch.setattr(refresh, "at_waterlooworks", lambda page: True)

    def limited(page: object) -> None:
        raise ListingFetchError("Rate limited.", FailureCategory.RATE_LIMIT, status=429)

    monkeypatch.setattr(refresh, "open_all_jobs", limited)
    service._run(run_id)
    [result] = seen
    assert (result.authenticated, result.error_category, result.pacing_stop) == (
        True,
        "rate_limit",
        "ban_risk",
    )
    assert scheduled_failure(result) == "ban_risk"
    connection = open_database(service.path)
    try:
        assert (
            connection.execute("SELECT status FROM collection_runs").fetchone()[0]
            == "failed"
        )
    finally:
        connection.close()
