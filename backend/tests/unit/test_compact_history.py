"""Real empty SQLite and generic payload primitives; no posting fixtures."""

import sqlite3
from pathlib import Path

import pytest
from pydantic import JsonValue

from waterlooworks_flash_next.compact_codec import HistoryError, canonical_text, digest
from waterlooworks_flash_next.compact_history import HistoryReader, store_payload
from waterlooworks_flash_next.database import open_database


def test_payload_dedup_checkpoints_and_immutability(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "empty.sqlite3")
    # Generic keys make small structural changes smaller than snapshots.
    value: dict[str, JsonValue] = {str(i): str(i) * 10 for i in range(1000)}
    original = canonical_text(value)
    with connection:
        first = store_payload(connection, "listing", original, digest(original))
        previous = first
        for step in range(70):
            value[str(step)] = "changed"
            text = canonical_text(value)
            previous = store_payload(
                connection, "listing", text, digest(text), previous
            )
            assert (
                HistoryReader(connection).read(previous, digest(text), "listing")
                == text
            )
        assert (
            store_payload(connection, "listing", original, digest(original), previous)
            == first
        )
    assert (
        connection.execute("SELECT max(depth) FROM history_payloads").fetchone()[0]
        == 32
    )
    assert (
        connection.execute(
            "SELECT count(*) FROM history_payloads WHERE base_id IS NULL"
        ).fetchone()[0]
        >= 3
    )
    for sql in ["UPDATE history_payloads SET depth=0", "DELETE FROM history_payloads"]:
        with pytest.raises(sqlite3.IntegrityError), connection:
            connection.execute(sql)
    with pytest.raises(HistoryError):
        HistoryReader(connection).read(first, "0" * 64, "listing")
    with pytest.raises(HistoryError):
        HistoryReader(connection).read(first, digest(original), "content")
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()


def test_snapshot_fallback_and_corruption_rejected(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "empty.sqlite3")
    with connection:
        text = canonical_text(None)
        first = store_payload(connection, "content", text, digest(text))
        other = canonical_text(True)
        second = store_payload(connection, "content", other, digest(other), first)
    assert (
        connection.execute(
            "SELECT base_id FROM history_payloads WHERE payload_id=?", (second,)
        ).fetchone()[0]
        is None
    )
    # Deliberately corrupt an isolated generic codec store, never source postings.
    connection.execute("DROP TRIGGER immutable_payload_update")
    with connection:
        connection.execute(
            "UPDATE history_payloads SET data=x'00' WHERE payload_id=?", (first,)
        )
    with pytest.raises(HistoryError):
        HistoryReader(connection).read(first, digest(text), "content")
    connection.close()


def test_v6_migration_rolls_back_removed_columns_and_triggers(tmp_path: Path) -> None:
    from waterlooworks_flash_next.content_schema import migrate_content
    from waterlooworks_flash_next.database import (
        _SCHEMA,
        DatabaseError,
        _migrate_history,
    )
    from waterlooworks_flash_next.detail_schema import migrate_details
    from waterlooworks_flash_next.observations import migrate_observations

    path = tmp_path / "v5.sqlite3"
    with sqlite3.connect(path) as connection:
        for statement in _SCHEMA:
            connection.execute(statement)
        _migrate_history(connection)
        migrate_details(connection)
        migrate_content(connection)
        migrate_observations(connection)
        connection.execute("PRAGMA user_version=5")
        # An unknown dependent view blocks removal after listing migration has run.
        connection.execute(
            "CREATE VIEW retained_content AS SELECT content_json "
            "FROM posting_content_revisions"
        )
        before = connection.execute(
            "SELECT type,name,sql FROM sqlite_master ORDER BY name"
        ).fetchall()
    with pytest.raises(DatabaseError, match="initialization failed"):
        open_database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
        assert (
            connection.execute(
                "SELECT type,name,sql FROM sqlite_master ORDER BY name"
            ).fetchall()
            == before
        )
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_payload_reader_rejects_corrupt_dependency_and_encoding(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "empty.sqlite3")
    value: dict[str, JsonValue] = {str(i): str(i) * 10 for i in range(100)}
    first = canonical_text(value)
    with connection:
        base_id = store_payload(connection, "listing", first, digest(first))
        value["0"] = None
        second = canonical_text(value)
        payload_id = store_payload(
            connection, "listing", second, digest(second), base_id
        )
    assert (
        connection.execute(
            "SELECT base_id FROM history_payloads WHERE payload_id=?", (payload_id,)
        ).fetchone()[0]
        == base_id
    )
    connection.execute("DROP TRIGGER immutable_payload_update")
    with connection:
        connection.execute(
            "UPDATE history_payloads SET depth=2 WHERE payload_id=?", (payload_id,)
        )
    with pytest.raises(HistoryError):
        HistoryReader(connection).read(payload_id, digest(second), "listing")
    connection.close()


def test_large_states_choose_checkpoint_instead_of_expensive_chains(
    tmp_path: Path,
) -> None:
    from waterlooworks_flash_next.compact_history import MAX_DELTA_SNAPSHOT_BYTES

    connection = open_database(tmp_path / "empty.sqlite3")
    first = canonical_text("x" * MAX_DELTA_SNAPSHOT_BYTES)
    second = canonical_text("x" * MAX_DELTA_SNAPSHOT_BYTES + "y")
    with connection:
        base_id = store_payload(connection, "content", first, digest(first))
        target = store_payload(connection, "content", second, digest(second), base_id)
    assert (
        connection.execute(
            "SELECT base_id FROM history_payloads WHERE payload_id=?", (target,)
        ).fetchone()[0]
        is None
    )
    assert HistoryReader(connection).read(target, digest(second), "content") == second
    connection.close()
