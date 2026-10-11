"""Schema17 scheduled refresh: validation, DST, slots and the persisted toggle.

Empty SQLite databases and primitive times only. No posting data, platform
responses or network access; nothing here starts a real refresh.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from waterlooworks_flash_next import database
from waterlooworks_flash_next.database import open_database, start_collection_run
from waterlooworks_flash_next.refresh_schedule import (
    MAX_TIMES,
    TORONTO,
    ScheduleError,
    ScheduleStore,
    due_slots,
    format_minute,
    next_run,
    parse_time,
    slot_instant,
    validate_new_time,
)


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def test_fresh_database_reaches_v17_with_empty_schedule(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "fresh.sqlite3")
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 17
    for table in ("refresh_schedule_times", "refresh_schedule_slots"):
        assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    state = connection.execute(
        "SELECT enabled, enabled_at, failed_run_id FROM refresh_schedule_state"
    ).fetchall()
    assert [tuple(row) for row in state] == [(0, None, None)]
    connection.close()


def test_v16_database_migrates_to_v17_preserving_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "legacy.sqlite3"
    with monkeypatch.context() as pinned:
        pinned.setattr(database, "SCHEMA_VERSION", 16)
        connection = open_database(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 16
    run_id = start_collection_run(connection)
    objects = {
        tuple(row)
        for row in connection.execute("SELECT type,name,sql FROM sqlite_master")
    }
    connection.close()
    connection = open_database(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 17
    assert objects <= {
        tuple(row)
        for row in connection.execute("SELECT type,name,sql FROM sqlite_master")
    }
    assert (
        connection.execute("SELECT id FROM collection_runs").fetchall()[0][0] == run_id
    )
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    connection.close()


def test_schema_constraints_reject_out_of_range_rows(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "empty.sqlite3")
    now = datetime.now(UTC).isoformat()
    for minute in (-1, 1440):
        with pytest.raises(sqlite3.IntegrityError), connection:
            connection.execute(
                "INSERT INTO refresh_schedule_times VALUES(?,?)", (minute, now)
            )
    with pytest.raises(sqlite3.IntegrityError), connection:
        connection.execute(
            "INSERT INTO refresh_schedule_slots(slot,due_at,outcome,decided_at) "
            "VALUES('2026-10-10 07:30',?,'invented',?)",
            (now, now),
        )
    with pytest.raises(sqlite3.IntegrityError), connection:
        connection.execute(
            "INSERT INTO refresh_schedule_state(id,changed_at) VALUES(2,?)", (now,)
        )
    connection.close()


@pytest.mark.parametrize(
    ("text", "minute"), [("00:00", 0), ("07:30", 450), ("23:59", 1439)]
)
def test_parse_and_format_round_trip(text: str, minute: int) -> None:
    assert parse_time(text) == minute
    assert format_minute(minute) == text


@pytest.mark.parametrize(
    "text", ["24:00", "7:30", "07:60", "0730", " 07:30", "07:30 ", "٠٧:٣٠", ""]
)
def test_parse_rejects_malformed_times_with_a_fixed_message(text: str) -> None:
    with pytest.raises(ScheduleError) as caught:
        parse_time(text)
    assert str(caught.value) == "Time must be HH:MM in 24-hour Toronto time."
    assert text.strip() == "" or text not in str(caught.value)


def test_validation_rejects_duplicates_close_times_across_midnight_and_overflow() -> (
    None
):
    with pytest.raises(ScheduleError, match="already scheduled"):
        validate_new_time([450], 450, 30)
    with pytest.raises(ScheduleError) as close:
        validate_new_time([450], 479, 30)
    assert str(close.value) == "Scheduled times must be at least 30 minutes apart."
    validate_new_time([450], 480, 30)
    validate_new_time([450], 420, 30)
    # Circular: 23:50 and 00:10 are 20 minutes apart.
    with pytest.raises(ScheduleError, match="30 minutes apart"):
        validate_new_time([1430], 10, 30)
    validate_new_time([1430], 20, 30)
    # A zero minimum interval still rejects exact duplicates only.
    validate_new_time([450], 451, 0)
    full = list(range(0, 1440, 60))
    assert len(full) == MAX_TIMES
    with pytest.raises(ScheduleError) as overflow:
        validate_new_time(full, 30, 0)
    assert str(overflow.value) == "At most 24 scheduled times are allowed."


def test_slot_instant_handles_dst_gap_and_overlap() -> None:
    # 2026-03-08 02:30 does not exist in Toronto: fires at the shifted instant.
    assert slot_instant(datetime(2026, 3, 8).date(), 150) == utc("2026-03-08 07:30")
    # 2026-11-01 01:30 happens twice: only the first occurrence.
    assert slot_instant(datetime(2026, 11, 1).date(), 90) == utc("2026-11-01 05:30")
    assert slot_instant(datetime(2026, 7, 1).date(), 450) == utc("2026-07-01 11:30")
    assert slot_instant(datetime(2026, 1, 15).date(), 450) == utc("2026-01-15 12:30")


def test_due_slots_respect_grace_window_and_enable_time() -> None:
    grace = timedelta(minutes=10)
    times = [450]  # 07:30 Toronto, 11:30 UTC in July
    assert due_slots(utc("2026-07-01 11:29:59"), times, None, grace) == []
    due = due_slots(utc("2026-07-01 11:30:00"), times, None, grace)
    assert due == [("2026-07-01 07:30", utc("2026-07-01 11:30"))]
    assert due_slots(utc("2026-07-01 11:39:59"), times, None, grace) == due
    # Missed slots older than the grace window never catch up (downtime).
    assert due_slots(utc("2026-07-01 11:40:00"), times, None, grace) == []
    # Re-enabling after a slot came due must not fire it.
    assert (
        due_slots(utc("2026-07-01 11:35"), times, utc("2026-07-01 11:31"), grace) == []
    )
    assert due_slots(utc("2026-07-01 11:35"), times, utc("2026-07-01 11:30"), grace)


def test_due_slots_cross_midnight_and_dst_days() -> None:
    grace = timedelta(minutes=10)
    # 23:55 Toronto slot observed just after local midnight is still in grace.
    assert due_slots(utc("2026-07-02 04:00"), [1435], None, grace) == [
        ("2026-07-01 23:55", utc("2026-07-02 03:55"))
    ]
    # Spring-forward nonexistent 02:30 fires once at 03:30 EDT.
    assert due_slots(utc("2026-03-08 07:31"), [150], None, grace) == [
        ("2026-03-08 02:30", utc("2026-03-08 07:30"))
    ]
    # Fall-back repeated 01:30: the second occurrence is not a new slot.
    assert due_slots(utc("2026-11-01 06:31"), [90], None, grace) == []


def test_next_run_is_the_next_future_instant() -> None:
    assert next_run(utc("2026-07-01 11:30"), [], None) is None
    assert next_run(utc("2026-07-01 11:30"), [450, 1200], None) == utc(
        "2026-07-02 00:00"
    )
    assert next_run(utc("2026-07-02 01:00"), [450, 1200], None) == utc(
        "2026-07-02 11:30"
    )
    assert TORONTO.key == "America/Toronto"


def test_store_adds_sorts_removes_and_validates(tmp_path: Path) -> None:
    store = ScheduleStore(tmp_path / "empty.sqlite3")
    store.add(1200, 30)
    store.add(450, 30)
    assert store.minutes() == [450, 1200]
    with pytest.raises(ScheduleError):
        store.add(460, 30)
    assert store.remove(450) is True
    assert store.remove(450) is False
    assert store.minutes() == [1200]


def test_slot_claim_is_atomic_and_single_use(tmp_path: Path) -> None:
    path = tmp_path / "empty.sqlite3"
    first, second = ScheduleStore(path), ScheduleStore(path)
    now = utc("2026-07-01 11:30")
    assert first.claim("2026-07-01 07:30", now, now) is True
    assert second.claim("2026-07-01 07:30", now, now) is False
    first.record("2026-07-01 07:30", "skipped_running", None, now)
    assert [entry.outcome for entry in second.recent()] == ["skipped_running"]


def test_toggle_records_enable_time_and_clears_failure(tmp_path: Path) -> None:
    store = ScheduleStore(tmp_path / "empty.sqlite3")
    at = utc("2026-07-01 11:00")
    store.set_enabled(True, at)
    state = store.state()
    assert state.enabled is True and state.enabled_at == at
    assert state.failure is None
    # A repeated "on" never moves the enable time (it would hide a due slot).
    store.set_enabled(True, at + timedelta(minutes=5))
    assert store.state().enabled_at == at
    store.set_enabled(False, at + timedelta(hours=1))
    assert store.state().enabled is False
    assert store.state().enabled_at is None
