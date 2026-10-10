"""Schema15 migration evidence on empty databases with operational rows only."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from waterlooworks_flash_next import database
from waterlooworks_flash_next.database import DatabaseError, open_database


def _job(
    connection: sqlite3.Connection,
    model: str,
    endpoint: str,
    *,
    concurrency: int = 1,
    max_tokens: int = 4096,
) -> str:
    job = str(uuid4())
    connection.execute(
        """INSERT INTO inference_jobs
        (job_id,status,model_id,endpoint,input_version,template_version,
         validator_version,max_tokens,concurrency,max_attempts,timeout_seconds,created_at)
        VALUES(?,'pending',?,?,'input','template','validator',?,?,1,120,?)""",
        (job, model, endpoint, max_tokens, concurrency, datetime.now(UTC).isoformat()),
    )
    return job


def test_schema15_keeps_historical_jobs_and_relaxes_provider_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "empty.sqlite3"
    with monkeypatch.context() as legacy:
        legacy.setattr(database, "SCHEMA_VERSION", 14)
        connection = open_database(path)
    with connection:
        _job(connection, "qwen3.8-27b@q4_k_m", "http://127.0.0.1:1234")
        _job(connection, "gpt-5.6-luna", "https://api.openai.com/v1/responses")
    before = [tuple(row) for row in connection.execute("SELECT * FROM inference_jobs")]
    objects = {
        tuple(row)
        for row in connection.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE name<>'inference_jobs'"
        )
    }
    connection.close()
    connection = open_database(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 15
    assert before == [
        tuple(row) for row in connection.execute("SELECT * FROM inference_jobs")
    ]
    assert objects <= {
        tuple(row)
        for row in connection.execute("SELECT type,name,sql FROM sqlite_master")
    }
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    for table in ("ai_settings", "ai_credentials"):
        assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    with connection:
        _job(
            connection,
            "pi:custom:ctx384k",
            "http://100.64.0.10:8000/v1",
            concurrency=8,
            max_tokens=32768,
        )
        _job(
            connection,
            "pi:codex_device:gpt-5.6-luna",
            "https://chatgpt.com/backend-api",
        )
    for model, endpoint, concurrency in (
        ("gpt-5.6-luna", "http://127.0.0.1:1234", 1),
        ("pi:custom:m", "ftp://example.invalid", 1),
        ("custom:m", "https://example.invalid", 1),
        ("pi:custom:m", "https://example.invalid", 9),
    ):
        with connection, pytest.raises(sqlite3.IntegrityError):
            _job(connection, model, endpoint, concurrency=concurrency)
    with connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO ai_credentials VALUES('other',zeroblob(200),'{}','now')"
        )
    connection.close()


def test_schema15_unknown_dependency_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "empty.sqlite3"
    with monkeypatch.context() as legacy:
        legacy.setattr(database, "SCHEMA_VERSION", 14)
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
        assert check.execute("PRAGMA user_version").fetchone()[0] == 14
        assert (
            before
            == check.execute("SELECT type,name,sql FROM sqlite_master").fetchall()
        )
