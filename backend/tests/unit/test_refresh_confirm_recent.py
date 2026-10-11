"""Confirmable post-run interval: only real empty control runs, no network.

Only collection_runs timing/request metadata is written; no postings,
platform responses or credentials are involved. Route tests replace
``RefreshService.start`` with a claim-only double so no worker, browser or
WaterlooWorks request can ever start.
"""

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from auth_support import session_client
from test_refresh_budget import finished_run

from waterlooworks_flash_next.api import create_app
from waterlooworks_flash_next.app_auth import COOKIE, SessionStore
from waterlooworks_flash_next.database import open_database
from waterlooworks_flash_next.refresh import (
    RECENT_HEADER,
    RefreshConflict,
    RefreshService,
    claim_refresh,
)
from waterlooworks_flash_next.refresh_budget import pacing_status
from waterlooworks_flash_next.refresh_schedule import RefreshScheduler, ScheduleStore
from waterlooworks_flash_next.ww_pacing import PacingConfig

CONFIG = PacingConfig(400, 1200, 30)


def test_recent_finish_needs_confirmation_and_names_the_finish_time(
    tmp_path: Path,
) -> None:
    path = tmp_path / "db.sqlite3"
    finished = datetime.now(UTC) - timedelta(minutes=12)
    finished_run(path, finished=finished, requests=5)
    with pytest.raises(RefreshConflict) as refusal:
        claim_refresh(path, CONFIG)
    assert refusal.value.status_code == 409
    assert refusal.value.recent_finished_at == finished.isoformat()
    assert "finished" in str(refusal.value)
    # No run is claimed by the refusal; the confirmed claim succeeds.
    run_id = claim_refresh(path, CONFIG, allow_recent=True)
    connection = open_database(path)
    try:
        assert connection.execute(
            "SELECT status FROM collection_runs WHERE id=?", (run_id,)
        ).fetchone()[0] == ("running")
    finally:
        connection.close()


def test_running_conflict_is_not_a_recent_signal(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    open_database(path).close()
    claim_refresh(path, CONFIG)
    with pytest.raises(RefreshConflict) as busy:
        claim_refresh(path, CONFIG, allow_recent=True)
    assert busy.value.status_code == 409
    assert busy.value.recent_finished_at is None


@pytest.mark.parametrize("allow_recent", [False, True])
def test_block_stays_a_hard_refusal_even_with_a_recent_finish(
    tmp_path: Path, allow_recent: bool
) -> None:
    path = tmp_path / "db.sqlite3"
    now = datetime.now(UTC)
    finished_run(
        path,
        finished=now - timedelta(minutes=10),
        requests=5,
        # Shorter than the remaining interval: the hard reason still wins.
        blocked_until=now + timedelta(minutes=5),
    )
    with pytest.raises(RefreshConflict) as refusal:
        claim_refresh(path, CONFIG, allow_recent=allow_recent)
    assert refusal.value.status_code == 429
    assert refusal.value.recent_finished_at is None
    assert 4 * 60 <= refusal.value.retry_after <= 5 * 60
    assert "rate-limit or sign-in" in str(refusal.value)


@pytest.mark.parametrize("allow_recent", [False, True])
def test_daily_budget_stays_a_hard_refusal(tmp_path: Path, allow_recent: bool) -> None:
    path = tmp_path / "db.sqlite3"
    finished_run(path, finished=datetime.now(UTC) - timedelta(minutes=5), requests=1198)
    with pytest.raises(RefreshConflict) as refusal:
        claim_refresh(path, CONFIG, allow_recent=allow_recent)
    assert refusal.value.status_code == 429
    assert "24-hour WaterlooWorks request budget" in str(refusal.value)


def test_pacing_status_separates_hard_wait_from_recent_window(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    now = datetime.now(UTC)
    finished = now - timedelta(minutes=10)
    finished_run(path, finished=finished, requests=5)
    connection = open_database(path)
    try:
        status = pacing_status(connection, CONFIG, now)
    finally:
        connection.close()
    assert status.last_finished_at == finished
    assert status.cooldown_until == finished + timedelta(minutes=30)
    # The interval alone no longer delays an explicit manual refresh.
    assert status.next_allowed_at is None
    assert status.hard_refusal(now) is None


def test_admin_status_and_snapshot_expose_the_recent_window(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    finished = datetime.now(UTC) - timedelta(minutes=12)
    finished_run(path, finished=finished, requests=5)
    service = RefreshService(path, None, pacing=CONFIG)
    service.restore()
    pacing = service.pacing_status()
    assert pacing.last_finished_at == finished.isoformat()
    assert pacing.cooldown_until == (finished + timedelta(minutes=30)).isoformat()
    assert pacing.next_allowed_at is None
    # Members see no countdown for a confirmable interval.
    assert service.snapshot().retry_after == 0


def test_scheduler_still_skips_during_the_interval(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    finished_run(path, finished=datetime.now(UTC) - timedelta(minutes=5), requests=5)

    class ClaimOnly:
        starts = 0

        def start(self) -> str:
            self.starts += 1
            return claim_refresh(path, CONFIG)

        def emit(self, message: str, level: str = "info") -> None:
            pass

        def set_schedule_notice(self, *_: str | None) -> None:
            pass

    store = ScheduleStore(path)
    service = ClaimOnly()
    instance = RefreshScheduler(store, service, active=True)
    now = datetime.now(UTC)
    assert store.claim("2026-10-11 07:30", now, now)
    instance._decide("2026-10-11 07:30")
    [entry] = store.recent()
    assert entry.outcome == "skipped_blocked" and entry.run_id is None
    connection = open_database(path)
    try:
        assert (
            connection.execute(
                "SELECT count(*) FROM collection_runs WHERE status='running'"
            ).fetchone()[0]
            == 0
        )
    finally:
        connection.close()


@pytest.fixture
def enabled_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, list[bool]]:
    path = tmp_path / "db.sqlite3"
    monkeypatch.setenv("WW_DATABASE", str(path))
    monkeypatch.setenv("WW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("WW_ENABLE_REFRESH", "1")
    monkeypatch.delenv("WW_ENABLE_SCHEDULE", raising=False)
    calls: list[bool] = []

    def claim_only(self: RefreshService, confirm_recent: bool = False) -> str:
        calls.append(confirm_recent)
        return claim_refresh(self.path, self.pacing, allow_recent=confirm_recent)

    monkeypatch.setattr(RefreshService, "start", claim_only)
    return path, calls


def test_route_answers_409_recent_then_accepts_confirmation(
    enabled_app: tuple[Path, list[bool]],
) -> None:
    path, calls = enabled_app
    open_database(path).close()
    finished = datetime.now(UTC) - timedelta(minutes=12)
    finished_run(path, finished=finished, requests=5)
    with session_client(path, app=create_app()) as client:
        refused = client.post("/api/refresh", json={})
        assert refused.status_code == 409
        assert refused.headers[RECENT_HEADER] == finished.isoformat()
        assert refused.headers["cache-control"] == "no-store"
        assert "retry-after" not in refused.headers
        assert set(refused.json()) == {"detail"}
        accepted = client.post("/api/refresh", json={"confirm_recent": True})
        assert accepted.status_code == 202
        assert accepted.json()["status"] == "running"
        # A second confirmation while running is the plain running conflict.
        busy = client.post("/api/refresh", json={"confirm_recent": True})
        assert busy.status_code == 409
        assert RECENT_HEADER not in busy.headers
    assert calls == [False, True, True]


def test_route_keeps_hard_refusals_as_429(
    enabled_app: tuple[Path, list[bool]],
) -> None:
    path, _ = enabled_app
    now = datetime.now(UTC)
    finished_run(
        path,
        finished=now - timedelta(minutes=10),
        requests=5,
        blocked_until=now + timedelta(hours=1),
    )
    with session_client(path, app=create_app()) as client:
        refused = client.post("/api/refresh", json={"confirm_recent": True})
        assert refused.status_code == 429
        assert int(refused.headers["retry-after"]) > 3_500
        assert RECENT_HEADER not in refused.headers


def test_route_body_is_strict_and_never_echoes_input(
    enabled_app: tuple[Path, list[bool]],
) -> None:
    path, calls = enabled_app
    with session_client(path, app=create_app()) as client:
        for body in (
            {"confirm_recent": "sentinel-value-123"},
            {"confirm_recent": True, "extra": "sentinel-value-123"},
        ):
            response = client.post("/api/refresh", json=body)
            assert response.status_code == 422
            assert "sentinel-value-123" not in response.text
        malformed = client.post(
            "/api/refresh",
            content='{"confirm_recent": sentinel-value-123',
            headers={"content-type": "application/json"},
        )
        assert malformed.status_code == 422
        assert "sentinel-value-123" not in malformed.text
    assert calls == []


def test_route_confirmation_still_requires_admin_and_request_guards(
    enabled_app: tuple[Path, list[bool]],
) -> None:
    path, calls = enabled_app
    finished_run(path, finished=datetime.now(UTC) - timedelta(minutes=5), requests=5)
    with session_client(path, app=create_app()) as client:
        body = {"confirm_recent": True}
        assert (
            client.post(
                "/api/refresh", json=body, headers={"origin": "https://example.org"}
            ).status_code
            == 403
        )
        client.headers.pop("x-ww-request")
        assert client.post("/api/refresh", json=body).status_code == 403
        assert calls == []
        client.headers["x-ww-request"] = "1"
        store = SessionStore(path)
        now = int(time.time())
        member = store.issue(
            store.prepare(None), "member-uid", now + 300, auth_time=now
        ).cookie
        client.cookies.clear()
        client.cookies.set(COOKIE, member, domain="localhost.local", path="/api")
        assert client.post("/api/refresh", json=body).status_code == 403
    assert calls == []


def test_openapi_documents_the_confirmation_contract(tmp_path: Path) -> None:
    with session_client(tmp_path / "db.sqlite3") as client:
        spec = client.app.openapi()  # type: ignore[attr-defined]
    request = spec["components"]["schemas"]["RefreshRequest"]
    assert request["properties"]["confirm_recent"]["type"] == "boolean"
    assert request["additionalProperties"] is False
    responses = spec["paths"]["/api/refresh"]["post"]["responses"]
    assert RECENT_HEADER in responses["409"]["headers"]
    assert "Retry-After" in responses["429"]["headers"]
    pacing = spec["components"]["schemas"]["RefreshPacingStatus"]["properties"]
    assert {"last_finished_at", "cooldown_until"} <= set(pacing)
    assert "300-second" not in spec["info"]["description"]
