"""Legacy v3 detail-table migration compatibility on empty SQLite databases.

The metadata-only detail storage APIs were removed; their empty tables remain so
existing databases keep migrating. Never fabricated postings.
"""

import sqlite3
from pathlib import Path

import pytest

from waterlooworks_flash_next.database import (
    _SCHEMA,
    SCHEMA_VERSION,
    DatabaseError,
    _migrate_history,
    open_database,
    start_collection_run,
)
from waterlooworks_flash_next.listing_fields import LISTING_FIELDS


def test_listing_allowlist_excludes_request_and_action_fields() -> None:
    assert set(LISTING_FIELDS) == {
        "Id",
        "JobTitle",
        "Organization",
        "Division",
        "Openings",
        "City",
        "Level",
        "ApplicationCount",
        "Deadline",
    }
    assert not {"action", "apply", "Cookie", "password", "username"} & set(
        LISTING_FIELDS
    )


def test_v2_migration_keeps_real_run_and_creates_empty_detail_tables(
    tmp_path: Path,
) -> None:
    path = tmp_path / "db.sqlite3"
    connection = sqlite3.connect(path)
    with connection:
        for statement in _SCHEMA:
            connection.execute(statement)
        _migrate_history(connection)
        connection.execute("PRAGMA user_version = 2")
    run_id = start_collection_run(connection)
    connection.close()
    connection = open_database(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert connection.execute("SELECT id FROM collection_runs").fetchone()[0] == run_id
    for table in (
        "posting_details",
        "posting_detail_revisions",
        "posting_fetch_attempts",
    ):
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert connection.execute(f"PRAGMA foreign_key_list({table})").fetchall()
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    connection.close()
    connection = open_database(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    connection.close()


def test_detail_migration_rolls_back_when_schema_conflicts(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    connection = sqlite3.connect(path)
    with connection:
        for statement in _SCHEMA:
            connection.execute(statement)
        _migrate_history(connection)
        connection.execute("PRAGMA user_version = 2")
        connection.execute(
            "CREATE TABLE posting_detail_revisions (revision_id INTEGER)"
        )
    connection.close()
    with pytest.raises(DatabaseError, match="initialization failed"):
        open_database(path)
    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    assert (
        connection.execute(
            "SELECT name FROM sqlite_master WHERE name='posting_details'"
        ).fetchall()
        == []
    )
    connection.close()
