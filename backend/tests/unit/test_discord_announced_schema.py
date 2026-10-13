"""Schema20 per-webhook announcement bookkeeping on empty SQLite.

Structural checks only: generic IDs, no webhook URLs, platform data or network.
"""

import sqlite3
from pathlib import Path

import pytest

from waterlooworks_flash_next import database
from waterlooworks_flash_next.data_generation import EXCLUDED_TABLES, read_generation
from waterlooworks_flash_next.database import open_database, start_collection_run

INSERT_WEBHOOK = (
    "INSERT INTO discord_webhooks(uid,label,ciphertext,hint,enabled,created_at) "
    "VALUES ('u1','Label',?,'…1234',1,1)"
)


def _with_webhook(path: Path) -> tuple[sqlite3.Connection, int]:
    connection = open_database(path)
    with connection:
        connection.execute("INSERT INTO app_users VALUES ('u1','member',1)")
        cursor = connection.execute(INSERT_WEBHOOK, (b"x" * 200,))
    return connection, int(cursor.lastrowid or 0)


def test_fresh_database_reaches_v20_with_an_empty_announcement_table(
    tmp_path: Path,
) -> None:
    connection = open_database(tmp_path / "fresh.sqlite3")
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 20
    assert database.SCHEMA_VERSION == 20
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(discord_webhook_announced)")
    }
    assert columns == {"webhook_id", "posting_id", "announced_at"}
    assert (
        connection.execute("SELECT count(*) FROM discord_webhook_announced").fetchone()[
            0
        ]
        == 0
    )
    connection.close()


def test_v19_database_migrates_to_v20_preserving_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "legacy.sqlite3"
    with monkeypatch.context() as pinned:
        pinned.setattr(database, "SCHEMA_VERSION", 19)
        connection = open_database(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 19
    run_id = start_collection_run(connection)
    with connection:
        connection.execute("INSERT INTO app_users VALUES ('u1','member',1)")
        connection.execute(INSERT_WEBHOOK, (b"x" * 200,))
    objects = {
        tuple(row)
        for row in connection.execute("SELECT type,name,sql FROM sqlite_master")
        if not str(row[1]).startswith("data_generation")
    }
    connection.close()
    connection = open_database(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 20
    assert objects <= {
        tuple(row)
        for row in connection.execute("SELECT type,name,sql FROM sqlite_master")
    }
    assert connection.execute("SELECT id FROM collection_runs").fetchone()[0] == run_id
    assert connection.execute("SELECT count(*) FROM discord_webhooks").fetchone()[0]
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    connection.close()


def test_announcements_are_unique_and_cascade_with_the_webhook(
    tmp_path: Path,
) -> None:
    connection, webhook = _with_webhook(tmp_path / "db.sqlite3")
    insert = "INSERT INTO discord_webhook_announced VALUES (?,?,?)"

    def rejected(parameters: tuple[object, ...]) -> None:
        with pytest.raises(sqlite3.IntegrityError), connection:
            connection.execute(insert, parameters)

    with connection:
        connection.execute(insert, (webhook, "100001", 5))
    rejected((webhook, "100001", 6))  # one row per webhook and posting
    rejected((webhook + 1, "100002", 5))  # unknown webhook
    rejected((webhook, "", 5))
    rejected((webhook, "12a", 5))
    rejected((webhook, "100002", 0))
    with connection:
        connection.execute("DELETE FROM discord_webhooks WHERE id=?", (webhook,))
    assert (
        connection.execute("SELECT count(*) FROM discord_webhook_announced").fetchone()[
            0
        ]
        == 0
    )
    connection.close()


def test_announcement_writes_never_invalidate_shared_snapshots(
    tmp_path: Path,
) -> None:
    assert "discord_webhook_announced" in EXCLUDED_TABLES
    connection, webhook = _with_webhook(tmp_path / "cache.sqlite3")
    before = read_generation(connection)
    with connection:
        connection.execute(
            "INSERT INTO discord_webhook_announced VALUES (?,'100001',1)", (webhook,)
        )
    assert read_generation(connection) == before
    connection.close()
