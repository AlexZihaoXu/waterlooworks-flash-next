"""Rolling 24-hour request budget, cooldown and blocks on real empty control runs.

Only collection_runs metadata (timestamps and request counts) is written; no
posting records, platform responses or credentials are involved.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from waterlooworks_flash_next.database import (
    SCHEMA_VERSION,
    open_database,
    start_collection_run,
)
from waterlooworks_flash_next.refresh import RefreshConflict, claim_refresh
from waterlooworks_flash_next.refresh_budget import (
    pacing_status,
    record_ww_request,
    rolling_requests,
)
from waterlooworks_flash_next.ww_pacing import PacingConfig


def finished_run(
    path: Path,
    *,
    finished: datetime,
    requests: int,
    blocked_until: datetime | None = None,
) -> str:
    connection = open_database(path)
    try:
        run = start_collection_run(connection)
        with connection:
            connection.execute(
                "UPDATE collection_runs SET started_at=?,status='completed',"
                "finished_at=?,blocked_until=? WHERE id=?",
                (
                    (finished - timedelta(minutes=5)).isoformat(),
                    finished.isoformat(),
                    blocked_until.isoformat() if blocked_until else None,
                    run,
                ),
            )
            connection.execute(
                "INSERT INTO collection_run_pacing(run_id,ww_requests) VALUES(?,?)",
                (run, requests),
            )
        return run
    finally:
        connection.close()


def test_schema_adds_an_additive_per_run_pacing_table(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "db.sqlite3")
    try:
        assert SCHEMA_VERSION == 15
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 15
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(collection_runs)")
        }
        assert not {"ww_requests", "pacing_stop"} & columns
        run = start_collection_run(connection)
        for statement in (
            "INSERT INTO collection_run_pacing(run_id,pacing_stop) VALUES(?,'other')",
            "INSERT INTO collection_run_pacing(run_id,ww_requests) VALUES(?,-1)",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                with connection:
                    connection.execute(statement, (run,))
        with pytest.raises(sqlite3.IntegrityError):
            with connection:
                connection.execute(
                    "INSERT INTO collection_run_pacing(run_id) VALUES('missing')"
                )
    finally:
        connection.close()


def test_migration_from_v14_keeps_existing_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from waterlooworks_flash_next import database

    path = tmp_path / "db.sqlite3"
    with monkeypatch.context() as legacy:
        legacy.setattr(database, "SCHEMA_VERSION", 14)
        connection = database.open_database(path)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 14
        run = start_collection_run(connection)
        connection.close()
    connection = open_database(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 15
        assert [
            tuple(row) for row in connection.execute("SELECT id FROM collection_runs")
        ] == [(run,)]
        assert rolling_requests(connection, datetime.now(UTC)) == 0
        assert (
            connection.execute("SELECT count(*) FROM collection_run_pacing").fetchone()[
                0
            ]
            == 0
        )
    finally:
        connection.close()


def test_request_counter_is_durable_per_running_run(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "db.sqlite3")
    try:
        run = start_collection_run(connection)
        for _ in range(3):
            record_ww_request(connection, run)
        assert (
            connection.execute(
                "SELECT ww_requests FROM collection_run_pacing WHERE run_id=?", (run,)
            ).fetchone()[0]
            == 3
        )
        with connection:
            connection.execute(
                "UPDATE collection_runs SET status='completed',finished_at=? "
                "WHERE id=?",
                (datetime.now(UTC).isoformat(), run),
            )
        record_ww_request(connection, run)  # finished runs never grow
        assert rolling_requests(connection, datetime.now(UTC)) == 3
    finally:
        connection.close()


def test_rolling_window_counts_only_runs_ending_in_the_last_day(
    tmp_path: Path,
) -> None:
    path = tmp_path / "db.sqlite3"
    now = datetime.now(UTC)
    finished_run(path, finished=now - timedelta(hours=25), requests=900)
    finished_run(path, finished=now - timedelta(hours=23), requests=300)
    recent = finished_run(path, finished=now - timedelta(hours=1), requests=200)
    connection = open_database(path)
    try:
        assert rolling_requests(connection, now) == 500
        assert rolling_requests(connection, now, exclude_run=recent) == 300
    finally:
        connection.close()


def test_status_reports_usage_cooldown_block_and_next_allowed(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    now = datetime.now(UTC)
    finished_run(
        path,
        finished=now - timedelta(minutes=10),
        requests=100,
        blocked_until=now + timedelta(hours=2),
    )
    connection = open_database(path)
    try:
        status = pacing_status(connection, PacingConfig(400, 1200, 30), now)
    finally:
        connection.close()
    assert status.used == 100 and status.remaining == 1100
    assert status.cooldown_until == now - timedelta(minutes=10) + timedelta(minutes=30)
    assert status.blocked_until == now + timedelta(hours=2)
    assert status.next_allowed_at == status.blocked_until
    assert status.refusal(now) is not None
    assert status.refusal(now)[0] == "blocked"  # type: ignore[index]


def test_exhausted_budget_recovers_when_old_runs_leave_the_window(
    tmp_path: Path,
) -> None:
    path = tmp_path / "db.sqlite3"
    now = datetime.now(UTC)
    first = now - timedelta(hours=20)
    finished_run(path, finished=first, requests=700)
    finished_run(path, finished=now - timedelta(hours=10), requests=500)
    connection = open_database(path)
    try:
        status = pacing_status(connection, PacingConfig(400, 1200, 0), now)
    finally:
        connection.close()
    assert status.remaining == 0
    assert status.budget_until == first + timedelta(hours=24)
    assert status.next_allowed_at == status.budget_until
    reason, delay = status.refusal(now)  # type: ignore[misc]
    assert reason == "daily_budget"
    assert 4 * 3600 - 5 <= delay <= 4 * 3600


def test_claim_refuses_when_rolling_budget_is_exhausted(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    finished_run(path, finished=datetime.now(UTC) - timedelta(hours=2), requests=1198)
    with pytest.raises(RefreshConflict) as refusal:
        claim_refresh(path, PacingConfig(400, 1200, 0))
    assert refusal.value.status_code == 429
    assert "24-hour WaterlooWorks request budget" in str(refusal.value)
    assert refusal.value.retry_after > 21 * 3600
    # A larger budget admits the same history.
    claim_refresh(path, PacingConfig(400, 1300, 0))


def test_claim_cooldown_uses_configured_minutes_and_reports_remaining(
    tmp_path: Path,
) -> None:
    path = tmp_path / "db.sqlite3"
    finished_run(path, finished=datetime.now(UTC) - timedelta(minutes=10), requests=5)
    with pytest.raises(RefreshConflict) as refusal:
        claim_refresh(path, PacingConfig(400, 1200, 30))
    assert refusal.value.status_code == 429
    assert 19 * 60 <= refusal.value.retry_after <= 20 * 60
    assert str(refusal.value) == (
        "Refresh cooldown is active; next refresh allowed in about 20 min."
    )
    claim_refresh(path, PacingConfig(400, 1200, 0))


def test_default_claim_uses_thirty_minute_cooldown(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    finished_run(path, finished=datetime.now(UTC) - timedelta(minutes=20), requests=1)
    with pytest.raises(RefreshConflict) as refusal:
        claim_refresh(path)
    assert 9 * 60 <= refusal.value.retry_after <= 10 * 60
