"""Generic JSON/hash tests and real empty SQLite migrations; no posting fixtures."""

import hashlib
import sqlite3
from pathlib import Path

import pytest

from waterlooworks_flash_next.database import (
    _SCHEMA,
    SCHEMA_VERSION,
    DatabaseError,
    canonical_snapshot,
    open_database,
    save_listings,
    start_collection_run,
)


def test_canonical_hash_ignores_object_key_order() -> None:
    first = canonical_snapshot({"a": None, "b": [1, "é"]})
    assert first == canonical_snapshot({"b": [1, "é"], "a": None})
    text, digest = first
    assert digest == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert len(digest) == 64


def test_hash_distinguishes_changes_and_reversions() -> None:
    original = canonical_snapshot({})[1]
    changed = canonical_snapshot({"a": None})[1]
    assert original != changed
    assert canonical_snapshot({})[1] == original
    assert canonical_snapshot({"a": [1, 2]}) != canonical_snapshot({"a": [2, 1]})
    assert canonical_snapshot({"a": 1}) != canonical_snapshot({"a": "1"})


def test_nonfinite_json_is_rejected_safely() -> None:
    with pytest.raises(DatabaseError, match="canonical"):
        canonical_snapshot({"a": float("nan")})


def test_v1_migration_preserves_real_run_metadata(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    connection = sqlite3.connect(path)
    with connection:
        for statement in _SCHEMA:
            connection.execute(statement)
        connection.execute("PRAGMA user_version = 1")
    run_id = start_collection_run(connection)
    connection.close()
    connection = open_database(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert connection.execute("SELECT id FROM collection_runs").fetchone()[0] == run_id
    assert (
        connection.execute("SELECT COUNT(*) FROM posting_revisions").fetchone()[0] == 0
    )
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(posting_revisions)")
    }
    assert {
        "revision_id",
        "content_hash",
        "listing_json",
        "run_id",
        "observed_at",
        "is_baseline",
    } <= columns
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    triggers = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'"
        )
    }
    assert {
        "immutable_revision_update",
        "immutable_revision_delete",
        "require_hash_insert",
        "require_hash_update",
        "revision_matches_current",
    } <= triggers
    connection.close()


def test_failed_migration_rolls_back_schema_and_version(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    connection = sqlite3.connect(path)
    with connection:
        for statement in _SCHEMA:
            connection.execute(statement)
        connection.execute("PRAGMA user_version = 1")
        connection.execute("CREATE TABLE posting_revisions (revision_id INTEGER)")
    connection.close()
    with pytest.raises(DatabaseError, match="initialization failed"):
        open_database(path)
    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    assert "content_hash" not in {
        row[1] for row in connection.execute("PRAGMA table_info(postings)")
    }
    connection.close()


def test_empty_persistence_and_transaction_ownership(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "db.sqlite3")
    run_id = start_collection_run(connection)
    assert save_listings(connection, run_id, []) == 0
    with pytest.raises(DatabaseError):
        save_listings(connection, "", [])
    connection.execute("BEGIN")
    with pytest.raises(DatabaseError, match="transaction"):
        save_listings(connection, run_id, [])
    assert connection.in_transaction
    with pytest.raises(DatabaseError, match="transaction"):
        start_collection_run(connection)
    assert connection.in_transaction
    connection.rollback()
    assert (
        connection.execute("SELECT COUNT(*) FROM posting_revisions").fetchone()[0] == 0
    )
    connection.close()
