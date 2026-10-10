"""Schema14 migration evidence on empty DB plus real operational metadata only."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from waterlooworks_flash_next import database
from waterlooworks_flash_next.database import DatabaseError, open_database


def _job(connection: sqlite3.Connection, model: str, endpoint: str) -> str:
    job = str(uuid4())
    connection.execute(
        """INSERT INTO inference_jobs
        (job_id,status,model_id,endpoint,input_version,template_version,
         validator_version,max_tokens,concurrency,max_attempts,timeout_seconds,created_at)
        VALUES(?,'pending',?,?,'input','template','validator',4096,1,1,120,?)""",
        (job, model, endpoint, datetime.now(UTC).isoformat()),
    )
    return job


def test_schema14_preserves_legacy_rows_and_all_dependent_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "empty.sqlite3"
    with monkeypatch.context() as legacy:
        legacy.setattr(database, "SCHEMA_VERSION", 13)
        connection = open_database(path)
    with connection:
        _job(connection, "qwen3.8-27b@q4_k_m", "http://127.0.0.1:1234")
    before = [tuple(row) for row in connection.execute("SELECT * FROM inference_jobs")]
    objects = [
        tuple(row)
        for row in connection.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE name<>'inference_jobs'"
        )
    ]
    connection.close()
    monkeypatch.setattr(database, "SCHEMA_VERSION", 14)
    connection = open_database(path)
    assert before == [
        tuple(row) for row in connection.execute("SELECT * FROM inference_jobs")
    ]
    assert set(objects) <= {
        tuple(row)
        for row in connection.execute("SELECT type,name,sql FROM sqlite_master")
    }
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 14
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with connection:
        _job(connection, "gpt-5.6-luna", "https://api.openai.com/v1/responses")
        _job(connection, "qwen3.8-27b@q4_k_m", "http://127.0.0.1:1234")
    for model, endpoint in (
        ("gpt-5.6-luna", "http://127.0.0.1:1234"),
        ("qwen3.8-27b@q4_k_m", "https://api.openai.com/v1/responses"),
    ):
        with connection, pytest.raises(sqlite3.IntegrityError):
            _job(connection, model, endpoint)
    connection.close()


def test_schema14_unknown_dependency_rolls_back_without_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "empty.sqlite3"
    with monkeypatch.context() as legacy:
        legacy.setattr(database, "SCHEMA_VERSION", 13)
        connection = open_database(path)
    with connection:
        connection.execute(
            "CREATE VIEW operator_dependency AS SELECT job_id FROM inference_jobs"
        )
    before = [
        tuple(row)
        for row in connection.execute("SELECT type,name,sql FROM sqlite_master")
    ]
    connection.close()
    with pytest.raises(DatabaseError):
        open_database(path)
    with sqlite3.connect(path) as check:
        assert check.execute("PRAGMA user_version").fetchone()[0] == 13
        assert (
            before
            == check.execute("SELECT type,name,sql FROM sqlite_master").fetchall()
        )
