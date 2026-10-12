"""Schema18 API tokens and per-user posting marks on empty SQLite.

Structural checks only: no posting content, platform responses or network.
"""

import sqlite3
from pathlib import Path

import pytest

from waterlooworks_flash_next import database
from waterlooworks_flash_next.data_generation import EXCLUDED_TABLES
from waterlooworks_flash_next.database import open_database, start_collection_run


def test_fresh_database_reaches_v18_with_empty_tables(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "fresh.sqlite3")
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 18
    for table in ("api_tokens", "posting_marks"):
        assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    tokens = {row[1] for row in connection.execute("PRAGMA table_info(api_tokens)")}
    assert tokens == {"uid", "token_hash", "hint", "created_at", "last_used_at"}
    assert not {"token", "plaintext", "secret"} & tokens
    marks = {row[1] for row in connection.execute("PRAGMA table_info(posting_marks)")}
    assert marks == {"uid", "posting_id", "state", "viewed_at", "updated_at"}
    connection.close()


def test_v17_database_migrates_to_v18_preserving_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "legacy.sqlite3"
    with monkeypatch.context() as pinned:
        pinned.setattr(database, "SCHEMA_VERSION", 17)
        connection = open_database(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 17
    run_id = start_collection_run(connection)
    objects = {
        tuple(row)
        for row in connection.execute("SELECT type,name,sql FROM sqlite_master")
        if not str(row[1]).startswith("data_generation")
    }
    connection.close()
    connection = open_database(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 18
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


def test_token_and_mark_constraints(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "empty.sqlite3")

    def rejected(sql: str, parameters: tuple[object, ...] = ()) -> None:
        with pytest.raises(sqlite3.IntegrityError), connection:
            connection.execute(sql, parameters)

    with connection:
        connection.execute("INSERT INTO app_users VALUES ('u1','admin',1)")
    digest = "a" * 64
    rejected(  # unknown user
        "INSERT INTO api_tokens VALUES ('nobody',?, 'wwf_…abcd', 1, NULL)", (digest,)
    )
    rejected(  # not a sha256 hex digest
        "INSERT INTO api_tokens VALUES ('u1','short', 'wwf_…abcd', 1, NULL)"
    )
    with connection:
        connection.execute(
            "INSERT INTO api_tokens VALUES ('u1',?, 'wwf_…abcd', 1, NULL)", (digest,)
        )
    rejected(  # one active token per user
        "INSERT INTO api_tokens VALUES ('u1',?, 'wwf_…efgh', 2, NULL)", ("b" * 64,)
    )
    now = "2026-10-12T00:00:00.000000+00:00"
    rejected(  # marks need a saved posting
        "INSERT INTO posting_marks VALUES ('u1','1','interested',NULL,?)", (now,)
    )
    run_id = start_collection_run(connection)
    with connection:
        connection.execute(
            "INSERT INTO postings (posting_id, listing_json, content_hash, "
            "first_seen_at, last_seen_at, first_seen_run_id, last_seen_run_id) "
            "VALUES ('1', '{\"id\":\"1\"}', ?, ?, ?, ?, ?)",
            ("c" * 64, now, now, run_id, run_id),
        )
    for state, viewed in (("maybe", None), (None, None), ("applied", "later")):
        rejected(
            "INSERT INTO posting_marks VALUES ('u1','1',?,?,?)", (state, viewed, now)
        )
    with connection:
        connection.execute(
            "INSERT INTO posting_marks VALUES ('u1','1','applied',?,?)", (now, now)
        )
    rejected(  # one mark row per user/posting
        "INSERT INTO posting_marks VALUES ('u1','1',NULL,?,?)", (now, now)
    )
    connection.close()


def test_tokens_and_marks_never_invalidate_the_shared_snapshot_cache() -> None:
    # Per-user annotations and credentials never enter the identity-free cache.
    assert {"api_tokens", "posting_marks"} <= EXCLUDED_TABLES
