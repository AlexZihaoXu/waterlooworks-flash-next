"""Empty database and timestamp primitive contracts; no fabricated postings."""

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import JsonValue

from waterlooworks_flash_next.database import open_database
from waterlooworks_flash_next.observations import observation_series, utc_timestamp


def test_receipt_timestamp_requires_timezone_and_preserves_precision() -> None:
    value = datetime(2026, 1, 1, 1, 2, 3, 456789, timezone(timedelta(hours=1)))
    assert utc_timestamp(value) == "2026-01-01T00:02:03.456789+00:00"
    with pytest.raises(ValueError, match="timezone"):
        utc_timestamp(value.replace(tzinfo=None))
    assert utc_timestamp(datetime(2026, 1, 1, tzinfo=UTC)).endswith(".000000+00:00")


def test_observation_schema_and_empty_read(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "empty.sqlite3")
    assert observation_series(connection, "") == []
    columns = {
        r[1] for r in connection.execute("PRAGMA table_info(posting_observations)")
    }
    assert {
        "observation_id",
        "posting_id",
        "run_id",
        "revision_id",
        "observed_at",
        "timestamp_provenance",
    } <= columns
    assert "listing_json" not in columns
    triggers = {
        r[0]
        for r in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        )
    }
    assert {
        "immutable_observation_update",
        "immutable_observation_delete",
        "observation_matches_revision",
    } <= triggers
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()


def test_v4_observation_migration_failure_is_transactional(tmp_path: Path) -> None:
    import sqlite3

    from waterlooworks_flash_next.content_schema import migrate_content
    from waterlooworks_flash_next.database import (
        _SCHEMA,
        DatabaseError,
        _migrate_history,
    )
    from waterlooworks_flash_next.detail_schema import migrate_details

    path = tmp_path / "v4.sqlite3"
    with sqlite3.connect(path) as connection:
        for statement in _SCHEMA:
            connection.execute(statement)
        _migrate_history(connection)
        migrate_details(connection)
        migrate_content(connection)
        connection.execute("PRAGMA user_version=4")
        connection.execute(
            "CREATE INDEX idx_observation_series ON postings(posting_id)"
        )
    with pytest.raises(DatabaseError, match="initialization failed"):
        open_database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name='posting_observations'"
            ).fetchall()
            == []
        )
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_count_evidence_is_not_persistable(value: float) -> None:
    from waterlooworks_flash_next.database import DatabaseError, canonical_snapshot

    with pytest.raises(DatabaseError, match="canonical"):
        canonical_snapshot({"application_count": value})


def test_count_evidence_keeps_unknown_and_raw_types() -> None:
    from waterlooworks_flash_next.database import canonical_snapshot

    values: list[dict[str, JsonValue]] = [
        {},
        {"application_count": None},
        {"application_count": 0},
        {"application_count": "0"},
        {"application_count": False},
    ]
    assert len({canonical_snapshot(value)[1] for value in values}) == len(values)
