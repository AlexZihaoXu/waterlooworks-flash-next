"""Primitive SQLite cache freshness checks; no posting fixtures."""

import sqlite3
from pathlib import Path

from waterlooworks_flash_next.snapshot_cache import SnapshotCache


def test_cache_invalidates_on_commits_and_does_not_publish_racing_load(
    tmp_path: Path,
) -> None:
    path = tmp_path / "primitive.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE probe(value INTEGER)")
    calls = 0
    race = False

    def load() -> int:
        nonlocal calls, race
        calls += 1
        with sqlite3.connect(path) as reader:
            value = int(reader.execute("SELECT count(*) FROM probe").fetchone()[0])
        if race:
            race = False
            with sqlite3.connect(path) as writer:
                writer.execute("INSERT INTO probe VALUES(7)")
        return value

    cache = SnapshotCache[int](path)
    try:
        assert cache.get(load) == 0
        assert cache.get(load) == 0 and calls == 1
        with sqlite3.connect(path) as writer:
            writer.execute("INSERT INTO probe VALUES(7)")
        assert cache.get(load) == 1 and calls == 2
        with sqlite3.connect(path) as writer:
            writer.execute("INSERT INTO probe VALUES(7)")
        race = True
        assert cache.get(load) == 2
        assert cache.get(load) == 3 and calls == 4
    finally:
        cache.close()


def test_session_writes_do_not_invalidate_but_data_writes_do(tmp_path: Path) -> None:
    from waterlooworks_flash_next.app_auth import SessionStore
    from waterlooworks_flash_next.database import open_database

    path = tmp_path / "app.sqlite3"
    open_database(path).close()
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE probe(value INTEGER)")
    open_database(path).close()  # Every open maintains generation triggers.
    calls = 0

    def load() -> int:
        nonlocal calls
        calls += 1
        return calls

    cache = SnapshotCache[int](path)
    try:
        assert cache.get(load) == 1
        sessions = SessionStore(path)
        cookie = sessions.prepare(None)
        sessions.logout(cookie)
        assert cache.get(load) == 1
        with sqlite3.connect(path) as writer:
            writer.execute("INSERT INTO probe VALUES(7)")
        assert cache.get(load) == 2
        with sqlite3.connect(path) as writer:
            writer.execute("UPDATE probe SET value=8")
        assert cache.get(load) == 3
        # A missing trigger falls back to invalidating on every commit.
        with sqlite3.connect(path) as writer:
            writer.execute("DROP TRIGGER data_generation_probe_update")
        assert cache.get(load) == 4
        sessions.prepare(None)
        assert cache.get(load) == 5
    finally:
        cache.close()


def test_generation_triggers_skip_auth_and_operational_tables(tmp_path: Path) -> None:
    from waterlooworks_flash_next.data_generation import EXCLUDED_TABLES
    from waterlooworks_flash_next.database import open_database

    connection = open_database(tmp_path / "app.sqlite3")
    try:
        triggers = {
            (row[0], row[1])
            for row in connection.execute(
                "SELECT tbl_name,name FROM sqlite_master WHERE type='trigger' "
                "AND name GLOB 'data_generation_*'"
            )
        }
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT GLOB 'sqlite_*'"
            )
        }
        assert {"app_sessions", "app_users", "collection_runs"} <= EXCLUDED_TABLES
        assert not {table for table, _ in triggers} & EXCLUDED_TABLES
        for table in tables - EXCLUDED_TABLES - {"data_generation"}:
            for operation in ("insert", "update", "delete"):
                assert (table, f"data_generation_{table}_{operation}") in triggers
    finally:
        connection.close()
    connection = open_database(tmp_path / "app.sqlite3")
    try:
        # A complete trigger set is not rewritten on every open.
        assert connection.total_changes == 0
        assert not connection.in_transaction
    finally:
        connection.close()
