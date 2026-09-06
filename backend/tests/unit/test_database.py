"""Real temporary SQLite databases; schema and run metadata only, no mock postings."""

import sqlite3
from pathlib import Path

import pytest

from waterlooworks_flash_next.database import (
    SCHEMA_VERSION,
    DatabaseError,
    open_database,
    start_collection_run,
)


def test_initialization_is_idempotent_and_enables_safety(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    connection = open_database(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    run_id = start_collection_run(connection)
    connection.close()
    connection = open_database(path)
    assert connection.execute("SELECT COUNT(*) FROM collection_runs").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM postings").fetchone()[0] == 0
    assert connection.execute("SELECT id FROM collection_runs").fetchone()[0] == run_id
    connection.close()


def test_run_constraints_use_real_empty_run_metadata(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "db.sqlite3")
    run_id = start_collection_run(connection)
    for sql in (
        "UPDATE collection_runs SET status = 'completed' WHERE id = ?",
        "UPDATE collection_runs SET pages_completed = -1 WHERE id = ?",
        "UPDATE collection_runs SET unique_postings = 1 WHERE id = ?",
    ):
        with pytest.raises(sqlite3.IntegrityError), connection:
            connection.execute(sql, (run_id,))
    connection.close()


def test_posting_columns_indexes_and_run_foreign_keys(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "db.sqlite3")
    columns = {row[1] for row in connection.execute("PRAGMA table_xinfo(postings)")}
    assert {
        "posting_id",
        "listing_json",
        "title",
        "organization",
        "first_seen_at",
        "last_seen_at",
        "first_seen_run_id",
        "last_seen_run_id",
    } <= columns
    assert not {"cookies", "action", "password", "username", "salary"} & columns
    foreign_keys = connection.execute("PRAGMA foreign_key_list(postings)").fetchall()
    assert {row[3] for row in foreign_keys} == {"first_seen_run_id", "last_seen_run_id"}
    assert {row[2] for row in foreign_keys} == {"collection_runs"}
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    connection.close()


def test_newer_database_is_not_downgraded(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    with pytest.raises(DatabaseError, match="newer"):
        open_database(path)
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute("PRAGMA user_version").fetchone()[0]
            == SCHEMA_VERSION + 1
        )
