"""Schema/model tests with empty SQLite databases, never fabricated postings."""

import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from waterlooworks_flash_next.database import (
    _SCHEMA,
    SCHEMA_VERSION,
    DatabaseError,
    _migrate_history,
    open_database,
    start_collection_run,
)
from waterlooworks_flash_next.detail_storage import (
    fail_detail_attempt,
    start_detail_attempt,
)
from waterlooworks_flash_next.details import DetailContent, DetailMetadata, StoredDetail


def test_detail_contract_keeps_unverified_content_null() -> None:
    assert set(DetailContent.model_fields) == {
        "work_term",
        "duration",
        "description",
        "responsibilities",
        "qualifications",
        "compensation_text",
        "work_arrangement",
        "location_details",
        "application_instructions",
    }
    assert set(DetailContent().model_dump().values()) == {None}
    with pytest.raises(ValidationError):
        DetailContent.model_validate({"description": False})
    with pytest.raises(ValidationError):
        DetailMetadata.model_validate({"action": None})
    assert StoredDetail.model_json_schema()["additionalProperties"] is False


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


def test_attempt_api_rejects_missing_identity_and_nested_transactions(
    tmp_path: Path,
) -> None:
    connection = open_database(tmp_path / "db.sqlite3")
    run_id = start_collection_run(connection)
    with pytest.raises(DatabaseError):
        start_detail_attempt(connection, run_id, "")
    with pytest.raises(DatabaseError):
        fail_detail_attempt(connection, "", "network")
    connection.execute("BEGIN")
    with pytest.raises(DatabaseError, match="transaction"):
        start_detail_attempt(connection, run_id, "")
    with pytest.raises(DatabaseError, match="transaction"):
        fail_detail_attempt(connection, "", "network")
    assert connection.in_transaction
    connection.rollback()
    assert (
        connection.execute("SELECT COUNT(*) FROM posting_fetch_attempts").fetchone()[0]
        == 0
    )
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
