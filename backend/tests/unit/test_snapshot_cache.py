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
