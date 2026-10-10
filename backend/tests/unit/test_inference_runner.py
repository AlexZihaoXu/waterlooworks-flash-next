"""Runner scheduling contracts on structural queue rows; no postings or responses.

Queue rows carry only digit identifiers and statuses. The sidecar session is
replaced by an in-process stand-in, so no model is ever contacted, and results
are the empty validated extraction used by the existing acceptance tests.
"""

import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from waterlooworks_flash_next import inference as inference_module
from waterlooworks_flash_next.ai_settings import AIConfig, AISettingsStore
from waterlooworks_flash_next.database import open_database
from waterlooworks_flash_next.inference import (
    InferenceError,
    _complete,
    _Item,
    _ProviderError,
    _run_inference_job,
    create_inference_job,
)
from waterlooworks_flash_next.model_validation import ValidatedExtraction
from waterlooworks_flash_next.openai_ledger import migrate_openai_ledger
from waterlooworks_flash_next.pi_bridge import ItemResult

EMPTY = ValidatedExtraction(rates=(), skills=())
PAID = AIConfig(model_id="gpt-5.6-luna")
FREE = AIConfig(
    access_mode="custom",
    model_id="local-model",
    custom_base_url="http://100.64.0.10:8000/v1",
    custom_context_window=32768,
)


class FakeSession:
    """Stand-in for PiSession: one per run, closed exactly once."""

    def __init__(self, config: AIConfig = PAID, result: object = None) -> None:
        self.config = config
        self.result = result
        self.closed = 0

    def run_item(self, _item: object) -> ItemResult:
        if isinstance(self.result, Exception):
            raise self.result
        assert isinstance(self.result, ItemResult)
        return self.result

    def close(self) -> None:
        self.closed += 1


Complete = Callable[[_Item, FakeSession], ValidatedExtraction]


def _queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    items: int,
    complete: Complete,
    *,
    concurrency: int,
) -> sqlite3.Connection:
    path = tmp_path / "queue.sqlite3"
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """CREATE TABLE inference_jobs (
               job_id TEXT PRIMARY KEY, status TEXT, concurrency INTEGER,
               max_attempts INTEGER, stop_requested INTEGER DEFAULT 0,
               started_at TEXT, lease_expires_at TEXT, finished_at TEXT,
               error_category TEXT
           );
           CREATE TABLE inference_job_items (
               job_id TEXT, posting_id TEXT, ordinal INTEGER, status TEXT,
               attempt_count INTEGER DEFAULT 0, started_at TEXT,
               finished_at TEXT, error_category TEXT
           );"""
    )
    migrate_openai_ledger(connection)
    with connection:
        connection.execute(
            "INSERT INTO inference_jobs(job_id,status,concurrency,max_attempts) "
            "VALUES(?,?,?,1)",
            (JOB, "pending", concurrency),
        )
        connection.executemany(
            "INSERT INTO inference_job_items(job_id,posting_id,ordinal,status) "
            "VALUES(?,?,?,'pending')",
            [(JOB, str(index + 1), index) for index in range(items)],
        )

    def prepare(_connection: sqlite3.Connection, row: sqlite3.Row) -> _Item:
        return _Item(
            JOB, row["posting_id"], "0" * 64, "0" * 64, {}, None, False, False, ()
        )

    def accept(
        connection: sqlite3.Connection, row: sqlite3.Row, *_args: object
    ) -> None:
        connection.execute(
            "UPDATE inference_job_items SET status='accepted',finished_at=? "
            "WHERE job_id=? AND posting_id=?",
            (datetime.now(UTC).isoformat(), JOB, row["posting_id"]),
        )

    monkeypatch.setattr(inference_module, "_job_config", lambda *_args: PAID)
    monkeypatch.setattr(inference_module, "_open_session", lambda *_args: FakeSession())
    monkeypatch.setattr(inference_module, "_cache", lambda *_args: None)
    monkeypatch.setattr(inference_module, "_prepare_item", prepare)
    monkeypatch.setattr(inference_module, "_accept", accept)
    monkeypatch.setattr(inference_module, "_complete", complete)
    return connection


JOB = "00000000-0000-4000-8000-000000000001"


def _statuses(connection: sqlite3.Connection) -> dict[str, tuple[str, str | None]]:
    return {
        row[0]: (row[1], row[2])
        for row in connection.execute(
            "SELECT posting_id,status,error_category FROM inference_job_items"
        )
    }


def _job(connection: sqlite3.Connection) -> tuple[str, int, str | None]:
    row = connection.execute(
        "SELECT status,stop_requested,error_category FROM inference_jobs"
    ).fetchone()
    return row[0], row[1], row[2]


def test_transient_known_zero_failure_does_not_halt_remaining_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def complete(item: _Item, _provider: FakeSession) -> ValidatedExtraction:
        if item.posting_id == "1":
            raise _ProviderError("http_client", retryable=True)
        return EMPTY

    connection = _queue(tmp_path, monkeypatch, 3, complete, concurrency=1)
    _run_inference_job(connection, JOB)
    assert _statuses(connection) == {
        "1": ("failed", "http_client"),
        "2": ("accepted", None),
        "3": ("accepted", None),
    }
    assert _job(connection) == ("failed", 0, "internal")
    connection.close()


def test_consecutive_transient_failures_trip_the_halt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(inference_module, "TRANSIENT_HALT_THRESHOLD", 2)

    def complete(_item: _Item, _provider: FakeSession) -> ValidatedExtraction:
        raise _ProviderError("http_server", retryable=True)

    connection = _queue(tmp_path, monkeypatch, 4, complete, concurrency=1)
    _run_inference_job(connection, JOB)
    statuses = _statuses(connection)
    assert statuses["1"] == ("failed", "http_server")
    assert statuses["2"] == ("interrupted", "http_server")
    assert statuses["3"] == statuses["4"] == ("interrupted", "stopped")
    assert _job(connection) == ("interrupted", 1, "stopped")
    connection.close()


def test_unexpected_worker_exception_terminates_the_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def complete(_item: _Item, _provider: FakeSession) -> ValidatedExtraction:
        raise sqlite3.OperationalError("disk I/O error")

    connection = _queue(tmp_path, monkeypatch, 2, complete, concurrency=1)
    _run_inference_job(connection, JOB)
    assert _statuses(connection) == {
        "1": ("interrupted", "internal"),
        "2": ("interrupted", "stopped"),
    }
    status, stop, _ = _job(connection)
    assert (status, stop) == ("interrupted", 1)
    connection.close()


@pytest.mark.parametrize(
    ("config", "outcome", "category", "retryable", "halt"),
    [
        (PAID, "rate_limited", "http_client", True, False),
        (PAID, "http_server", "http_server", True, False),
        (PAID, "http_client", "http_client", False, True),
        (PAID, "auth", "configuration", False, True),
        (PAID, "budget_stopped", "configuration", False, True),
        (PAID, "ledger", "internal", False, True),
        (PAID, "timeout", "timeout", False, True),
        (FREE, "timeout", "timeout", True, False),
        (FREE, "max_turns", "response_schema", False, False),
        (FREE, "max_tool_calls", "response_schema", False, False),
        (FREE, "no_submission", "response_schema", False, False),
        (FREE, "evidence_rejected", "evidence_rejected", False, False),
        (FREE, "surprise", "internal", False, True),
    ],
)
def test_sidecar_outcomes_map_to_item_policy(
    config: AIConfig,
    outcome: str,
    category: str,
    retryable: bool,
    halt: bool,
) -> None:
    from waterlooworks_flash_next.inference_prompt import PiPrompt

    prompt = PiPrompt("system", "user", (("A:", "text"),), {"type": "object"})
    item = _Item(JOB, "1", "0" * 64, "0" * 64, {"A:": "text"}, prompt, False, False, ())
    session = FakeSession(config, ItemResult("failed", None, outcome, 1, 1))
    with pytest.raises(_ProviderError) as error:
        _complete(item, session)  # type: ignore[arg-type]
    assert (error.value.category, error.value.retryable, error.value.halt) == (
        category,
        retryable,
        halt,
    )


def test_submitted_payload_is_revalidated_by_the_backend() -> None:
    from waterlooworks_flash_next.inference_prompt import PiPrompt

    prompt = PiPrompt("system", "user", (("A:", "text"),), {"type": "object"})
    item = _Item(JOB, "1", "0" * 64, "0" * 64, {"A:": "text"}, prompt, False, False, ())
    accepted = FakeSession(
        FREE, ItemResult("submitted", {"base_rates": [], "skills": []}, None, 2, 1)
    )
    assert _complete(item, accepted) == EMPTY  # type: ignore[arg-type]
    # The model returned an unrequested category: rejected despite "submitted".
    unrequested = {
        "base_rates": [],
        "skills": [
            {
                "skill": "Text",
                "requirement": "unspecified",
                "evidence": {"field": "A:", "text": "text"},
                "requirement_evidence": None,
            }
        ],
    }
    rejected = FakeSession(FREE, ItemResult("submitted", unrequested, None, 2, 1))
    with pytest.raises(_ProviderError) as error:
        _complete(item, rejected)  # type: ignore[arg-type]
    assert error.value.category == "evidence_rejected"
    crashed = FakeSession(FREE, RuntimeError("boom"))
    with pytest.raises(_ProviderError) as error:
        _complete(item, crashed)  # type: ignore[arg-type]
    assert (error.value.category, error.value.halt) == ("internal", True)


def test_runner_keeps_a_rolling_window_in_flight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slow call must not idle the other slot until the whole batch drains."""
    third_started = threading.Event()
    overlapped: list[bool] = []
    providers: set[int] = set()
    in_flight = 0
    peak = 0
    lock = threading.Lock()

    def complete(item: _Item, provider: FakeSession) -> ValidatedExtraction:
        nonlocal in_flight, peak
        with lock:
            providers.add(id(provider))
            in_flight += 1
            peak = max(peak, in_flight)
        try:
            if item.posting_id == "1":
                overlapped.append(third_started.wait(timeout=5))
            elif item.posting_id == "3":
                third_started.set()
            return EMPTY
        finally:
            with lock:
                in_flight -= 1

    connection = _queue(tmp_path, monkeypatch, 4, complete, concurrency=2)
    _run_inference_job(connection, JOB)
    assert overlapped == [True]
    assert peak == 2
    assert len(providers) == 1
    assert {status for status, _ in _statuses(connection).values()} == {"accepted"}
    assert _job(connection) == ("completed", 0, None)
    connection.close()


def test_stop_request_drains_in_flight_and_claims_nothing_new(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def complete(item: _Item, _provider: FakeSession) -> ValidatedExtraction:
        calls.append(item.posting_id)
        if item.posting_id == "1":
            stopper = sqlite3.connect(tmp_path / "queue.sqlite3", timeout=5)
            with stopper:
                stopper.execute("UPDATE inference_jobs SET stop_requested=1")
            stopper.close()
        return EMPTY

    connection = _queue(tmp_path, monkeypatch, 4, complete, concurrency=1)
    _run_inference_job(connection, JOB)
    assert calls == ["1"]
    statuses = _statuses(connection)
    assert statuses["1"] == ("accepted", None)
    assert statuses["2"] == statuses["3"] == ("interrupted", "stopped")
    assert _job(connection)[0] == "interrupted"
    connection.close()


def test_job_selection_does_not_hold_the_write_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Per-posting selection work runs before BEGIN IMMEDIATE is taken."""
    path = tmp_path / "inference.sqlite3"
    connection = open_database(path)
    writable: list[bool] = []

    def probe(*_args: object) -> object:
        other = sqlite3.connect(path, timeout=0)
        try:
            other.execute("BEGIN IMMEDIATE")
            other.rollback()
            writable.append(True)
        except sqlite3.OperationalError:
            writable.append(False)
        finally:
            other.close()
        raise InferenceError("Current normalized source is unavailable.")

    monkeypatch.setattr(inference_module, "_source", probe)
    store = AISettingsStore(path)
    store.save_draft(FREE)
    store.activate()
    with pytest.raises(InferenceError, match="source"):
        create_inference_job(connection, ["1"])
    assert writable == [True]
    assert not connection.in_transaction
    connection.close()
