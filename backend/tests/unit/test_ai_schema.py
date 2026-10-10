"""Schema16 migration evidence on empty databases with operational rows only.

Schema15 is the already-deployed WaterlooWorks pacing migration; the Pi AI
migration is applied after it as schema16.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from waterlooworks_flash_next import database
from waterlooworks_flash_next.database import (
    DatabaseError,
    open_database,
    start_collection_run,
)


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


def _rows(connection: sqlite3.Connection, table: str) -> list[tuple[object, ...]]:
    return [
        tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1")
    ]


def _open_at(
    path: Path, monkeypatch: pytest.MonkeyPatch, version: int
) -> sqlite3.Connection:
    with monkeypatch.context() as pinned:
        pinned.setattr(database, "SCHEMA_VERSION", version)
        return open_database(path)


def test_deployed_v15_pacing_database_migrates_to_v16_with_row_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "empty.sqlite3"
    connection = _open_at(path, monkeypatch, 14)
    with connection:
        _job(connection, "qwen3.8-27b@q4_k_m", "http://127.0.0.1:1234")
        _job(connection, "gpt-5.6-luna", "https://api.openai.com/v1/responses")
    connection.close()
    # The real pacing migration produces the deployed v15 database.
    connection = _open_at(path, monkeypatch, 15)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 15
    run_id = start_collection_run(connection)
    with connection:
        connection.execute(
            "INSERT INTO collection_run_pacing VALUES(?,?,?)",
            (run_id, 7, "request_cap"),
        )
    jobs, pacing, runs = (
        _rows(connection, "inference_jobs"),
        _rows(connection, "collection_run_pacing"),
        _rows(connection, "collection_runs"),
    )
    objects = {
        tuple(row)
        for row in connection.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE name<>'inference_jobs'"
        )
    }
    connection.close()
    connection = open_database(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 16
    assert _rows(connection, "inference_jobs") == jobs
    assert _rows(connection, "collection_run_pacing") == pacing
    assert _rows(connection, "collection_runs") == runs
    assert objects <= {
        tuple(row)
        for row in connection.execute("SELECT type,name,sql FROM sqlite_master")
    }
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    connection.close()


def test_fresh_and_v14_databases_reach_v16_through_pacing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fresh = open_database(tmp_path / "fresh.sqlite3")
    assert fresh.execute("PRAGMA user_version").fetchone()[0] == 16
    for table in ("collection_run_pacing", "ai_settings", "ai_credentials"):
        assert fresh.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    fresh.close()
    path = tmp_path / "legacy.sqlite3"
    _open_at(path, monkeypatch, 14).close()
    connection = open_database(path)  # v14 -> v15 (pacing) -> v16 (Pi)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 16
    assert connection.execute("SELECT count(*) FROM collection_run_pacing").fetchone()
    connection.close()


def test_schema16_relaxes_provider_checks_for_pi_jobs(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "empty.sqlite3")
    with connection:
        _job(connection, "qwen3.8-27b@q4_k_m", "http://127.0.0.1:1234")
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


def test_schema16_unknown_dependency_rolls_back_to_v15(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "empty.sqlite3"
    connection = _open_at(path, monkeypatch, 15)
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
        assert check.execute("PRAGMA user_version").fetchone()[0] == 15
        assert (
            before
            == check.execute("SELECT type,name,sql FROM sqlite_master").fetchall()
        )
