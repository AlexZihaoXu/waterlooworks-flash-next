"""Schema19 per-user Discord webhooks on empty SQLite.

Structural checks only: no webhook URLs, platform responses or network.
"""

import sqlite3
from pathlib import Path

import pytest

from waterlooworks_flash_next import database
from waterlooworks_flash_next.data_generation import EXCLUDED_TABLES, read_generation
from waterlooworks_flash_next.database import open_database, start_collection_run

COLUMNS = {
    "id",
    "uid",
    "label",
    "ciphertext",
    "hint",
    "enabled",
    "created_at",
    "last_sent_at",
    "last_status",
    "last_error_at",
}


def test_fresh_database_reaches_v19_with_an_empty_webhook_table(
    tmp_path: Path,
) -> None:
    connection = open_database(tmp_path / "fresh.sqlite3")
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 19
    assert database.SCHEMA_VERSION == 19
    assert (
        connection.execute("SELECT count(*) FROM discord_webhooks").fetchone()[0] == 0
    )
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(discord_webhooks)")
    }
    assert columns == COLUMNS
    assert not {"url", "token", "plaintext", "webhook_url"} & columns
    connection.close()


def test_v18_database_migrates_to_v19_preserving_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "legacy.sqlite3"
    with monkeypatch.context() as pinned:
        pinned.setattr(database, "SCHEMA_VERSION", 18)
        connection = open_database(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 18
    run_id = start_collection_run(connection)
    objects = {
        tuple(row)
        for row in connection.execute("SELECT type,name,sql FROM sqlite_master")
        if not str(row[1]).startswith("data_generation")
    }
    connection.close()
    connection = open_database(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 19
    assert objects <= {
        tuple(row)
        for row in connection.execute("SELECT type,name,sql FROM sqlite_master")
    }
    assert connection.execute("SELECT id FROM collection_runs").fetchone()[0] == run_id
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    connection.close()


def test_webhook_constraints(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "empty.sqlite3")
    blob = b"x" * 200

    def rejected(sql: str, parameters: tuple[object, ...] = ()) -> None:
        with pytest.raises(sqlite3.IntegrityError), connection:
            connection.execute(sql, parameters)

    insert = (
        "INSERT INTO discord_webhooks(uid,label,ciphertext,hint,enabled,created_at,"
        "last_sent_at,last_status,last_error_at) VALUES (?,?,?,?,?,?,?,?,?)"
    )
    with connection:
        connection.execute("INSERT INTO app_users VALUES ('u1','member',1)")
    rejected(insert, ("nobody", "Label", blob, "…1234", 1, 1, None, None, None))
    rejected(insert, ("u1", "", blob, "…1234", 1, 1, None, None, None))
    rejected(insert, ("u1", "x" * 61, blob, "…1234", 1, 1, None, None, None))
    rejected(insert, ("u1", "Label", b"short", "…1234", 1, 1, None, None, None))
    rejected(insert, ("u1", "Label", "text", "…1234", 1, 1, None, None, None))
    rejected(insert, ("u1", "Label", blob, "", 1, 1, None, None, None))
    rejected(insert, ("u1", "Label", blob, "…1234", 2, 1, None, None, None))
    rejected(insert, ("u1", "Label", blob, "…1234", 1, 0, None, None, None))
    rejected(insert, ("u1", "Label", blob, "…1234", 1, 1, None, "maybe", None))
    with connection:
        for status in (None, "ok", "failed", "gone"):
            connection.execute(
                insert, ("u1", "Label", blob, "…1234", 1, 1, 2, status, 3)
            )
    assert (
        connection.execute("SELECT count(*) FROM discord_webhooks").fetchone()[0] == 4
    )
    connection.close()


def test_webhook_writes_never_invalidate_shared_snapshots(tmp_path: Path) -> None:
    assert "discord_webhooks" in EXCLUDED_TABLES
    connection = open_database(tmp_path / "cache.sqlite3")
    with connection:
        connection.execute("INSERT INTO app_users VALUES ('u1','member',1)")
    before = read_generation(connection)
    assert before[0] == "generation"
    with connection:
        connection.execute(
            "INSERT INTO discord_webhooks(uid,label,ciphertext,hint,enabled,"
            "created_at) VALUES ('u1','Label',?,'…1234',1,1)",
            (b"x" * 200,),
        )
    assert read_generation(connection) == before
    connection.close()
