"""Real empty SQLite migrations/contracts; no fabricated posting records."""

import sqlite3
from pathlib import Path

import pytest

from waterlooworks_flash_next.content_storage import (
    fail_content_attempt,
    record_content_request,
    start_content_attempt,
)
from waterlooworks_flash_next.database import (
    _SCHEMA,
    SCHEMA_VERSION,
    DatabaseError,
    _migrate_history,
    open_database,
    start_collection_run,
)
from waterlooworks_flash_next.detail_schema import migrate_details


def _v3(path: Path) -> str:
    connection = sqlite3.connect(path)
    with connection:
        for statement in _SCHEMA:
            connection.execute(statement)
        _migrate_history(connection)
        migrate_details(connection)
        connection.execute("PRAGMA user_version=3")
    run = start_collection_run(connection)
    connection.close()
    return run


def test_v4_preserves_v3_schema_and_empty_run_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    run = _v3(path)
    connection = sqlite3.connect(path)
    legacy = connection.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' "
        "AND name<>'collection_runs' ORDER BY name"
    ).fetchall()
    connection.close()
    for _ in range(2):
        connection = open_database(path)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("SELECT id FROM collection_runs").fetchone()[0] == run
        for kind, name, sql in legacy:
            # v6 deliberately replaces listing revision JSON with compact evidence.
            if name in {
                "posting_revisions",
                "revision_matches_current",
                "immutable_revision_update",
            }:
                continue
            assert tuple(
                connection.execute(
                    "SELECT type,name,sql FROM sqlite_master WHERE name=?", (name,)
                ).fetchone()
            ) == (kind, name, sql)
        for table in (
            "posting_contents",
            "posting_content_revisions",
            "posting_content_attempts",
        ):
            assert (
                connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
            )
            assert connection.execute(f"PRAGMA foreign_key_list({table})").fetchall()
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        connection.close()


def test_v4_migration_failure_rolls_back_completely(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    _v3(path)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE posting_content_revisions(id INTEGER)")
    with pytest.raises(DatabaseError):
        open_database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert not connection.execute(
            "SELECT name FROM sqlite_master WHERE name='posting_contents'"
        ).fetchall()


def test_content_attempt_contract_rejects_missing_postings_and_terminal_ids(
    tmp_path: Path,
) -> None:
    connection = open_database(tmp_path / "db.sqlite3")
    run = start_collection_run(connection)
    with pytest.raises(DatabaseError):
        start_content_attempt(connection, run, "")
    with pytest.raises(DatabaseError):
        fail_content_attempt(connection, "", "network")
    with pytest.raises(DatabaseError):
        record_content_request(connection, "", retry=False)
    assert (
        connection.execute("SELECT count(*) FROM posting_content_attempts").fetchone()[
            0
        ]
        == 0
    )
    connection.close()
