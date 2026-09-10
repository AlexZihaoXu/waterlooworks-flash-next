"""Inference orchestration contracts on empty databases; no provider fixtures."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from waterlooworks_flash_next import inference as inference_module
from waterlooworks_flash_next.database import SCHEMA_VERSION, open_database
from waterlooworks_flash_next.inference import (
    MAX_TOKENS,
    InferenceConfig,
    InferenceError,
    _checkpoint_accept,
    _OwnershipLost,
    _prepare_resume,
    _recovered_item_status,
    _renew_lease,
    create_inference_job,
    inference_job_status,
    request_inference_stop,
    resume_inference_job,
)
from waterlooworks_flash_next.model_validation import ValidatedExtraction
from waterlooworks_flash_next.openai_provider import ENDPOINT
from waterlooworks_flash_next.openai_usage import MODEL as MODEL_ID


def test_v8_migration_creates_empty_durable_queue_without_starting_work(
    tmp_path: Path,
) -> None:
    connection = open_database(tmp_path / "inference.sqlite3")
    assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    for table in (
        "inference_jobs",
        "inference_job_items",
        "inference_cache",
        "posting_model_projection_current",
    ):
        assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    connection.close()


def test_configuration_is_exact_openai_low_effort_and_bounded() -> None:
    config = InferenceConfig()
    assert ENDPOINT == "https://api.openai.com/v1/responses"
    assert MODEL_ID == "gpt-5.6-luna"
    assert MAX_TOKENS == 4096
    assert config.concurrency <= 4
    for invalid in [{"concurrency": 5}, {"max_attempts": 4}]:
        with pytest.raises(ValidationError):
            InferenceConfig.model_validate(invalid)


def test_heartbeat_renews_only_current_lease_and_resume_rechecks_expiry(
    tmp_path: Path,
) -> None:
    connection = open_database(tmp_path / "inference.sqlite3")
    future = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
    with connection:
        connection.execute(
            """INSERT INTO inference_jobs
               (job_id,status,model_id,endpoint,input_version,template_version,
                validator_version,max_tokens,concurrency,max_attempts,
                timeout_seconds,created_at,started_at,lease_expires_at)
               VALUES('active','running','qwen3.8-27b@q4_k_m',
                'http://127.0.0.1:1234','input','template','validator',4096,1,2,
                600,?,?,?)""",
            (datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat(), future),
        )
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        renewed = _renew_lease(connection, "active", future)
    assert renewed != future
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(_OwnershipLost):
            _renew_lease(connection, "active", future)
    with pytest.raises(InferenceError, match="active"):
        _prepare_resume(connection, "active")
    assert (
        connection.execute(
            "SELECT lease_expires_at FROM inference_jobs WHERE job_id='active'"
        ).fetchone()[0]
        == renewed
    )
    connection.close()


def test_acceptance_rollback_reuses_committed_lease_for_failure_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """CREATE TABLE inference_jobs (
               job_id TEXT PRIMARY KEY, status TEXT, lease_expires_at TEXT
           );
           CREATE TABLE inference_job_items (
               job_id TEXT, posting_id TEXT, status TEXT,
               error_category TEXT, finished_at TEXT
           );"""
    )
    lease = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
    with connection:
        connection.execute(
            "INSERT INTO inference_jobs VALUES('job','running',?)", (lease,)
        )
        connection.execute(
            "INSERT INTO inference_job_items VALUES('job','item','running',NULL,NULL)"
        )
    item = connection.execute("SELECT * FROM inference_job_items").fetchone()

    def fail_accept(*_args: object) -> None:
        raise RuntimeError("forced acceptance failure")

    monkeypatch.setattr(inference_module, "_accept", fail_accept)
    committed = _checkpoint_accept(
        connection,
        "job",
        item,
        ValidatedExtraction(rates=(), skills=()),
        lease,
    )
    assert (
        connection.execute(
            "SELECT lease_expires_at FROM inference_jobs WHERE job_id='job'"
        ).fetchone()[0]
        == committed
    )
    failed = connection.execute(
        "SELECT status,error_category,finished_at FROM inference_job_items"
    ).fetchone()
    assert tuple(failed[:2]) == ("failed", "internal")
    assert failed[2] is not None
    connection.close()


def test_resume_policy_terminalizes_exhausted_abandoned_running_item() -> None:
    assert _recovered_item_status("running", 2, 2) == "failed"
    assert _recovered_item_status("running", 1, 2) == "pending"
    assert _recovered_item_status("interrupted", 1, 2) == "pending"
    assert _recovered_item_status("accepted", 1, 2) is None


def test_empty_database_operations_are_safe_and_do_not_contact_provider(
    tmp_path: Path,
) -> None:
    connection = open_database(tmp_path / "inference.sqlite3")
    with pytest.raises(InferenceError, match="posting selection"):
        create_inference_job(connection, [])
    with pytest.raises(InferenceError, match="source"):
        create_inference_job(connection, ["404"])
    for operation in (
        inference_job_status,
        request_inference_stop,
        resume_inference_job,
    ):
        with pytest.raises(InferenceError, match="job"):
            operation(connection, "missing")
    assert connection.execute("SELECT count(*) FROM inference_jobs").fetchone()[0] == 0
    connection.close()
