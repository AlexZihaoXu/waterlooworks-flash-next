"""Cooperative refresh interruption and explicit offline recovery.

Uses real empty migrations, empty control runs and identity-only sentinels; no
posting records, platform responses or provider responses are fabricated.
"""

import json
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi.testclient import TestClient

from waterlooworks_flash_next import detail_fetch, refresh
from waterlooworks_flash_next.api import create_app
from waterlooworks_flash_next.content import StoredPostingContent
from waterlooworks_flash_next.database import (
    StoredListing,
    open_database,
    start_collection_run,
)
from waterlooworks_flash_next.detail_fetch import DetailCollection, DetailSource
from waterlooworks_flash_next.listings import (
    CollectionInterrupted,
    ListingCollection,
    ListingPage,
    ListingSource,
)
from waterlooworks_flash_next.provider_lease import ProviderBusy, ProviderLease
from waterlooworks_flash_next.refresh import (
    RefreshConflict,
    RefreshService,
    claim_refresh,
    recover_interrupted_runs,
)
from waterlooworks_flash_next.ww_pacing import PacingConfig, RequestGate


def _running_control_run(path: Path) -> str:
    connection = open_database(path)
    try:
        return start_collection_run(connection)
    finally:
        connection.close()


def _run_row(path: Path, run_id: str) -> tuple[str, str | None, str | None]:
    connection = open_database(path)
    try:
        row = connection.execute(
            "SELECT status,finished_at,error_category FROM collection_runs WHERE id=?",
            (run_id,),
        ).fetchone()
        return str(row[0]), row[1], row[2]
    finally:
        connection.close()


def test_restore_publishes_a_stale_running_row_as_interrupted(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    run_id = _running_control_run(path)
    service = RefreshService(path, None)
    service.restore()
    snapshot = service.snapshot()
    assert snapshot.run_id == run_id
    assert snapshot.status == "interrupted"
    assert snapshot.stage == "recovery_required"
    # Restore only reports; the durable row is left for the explicit command.
    assert _run_row(path, run_id)[0] == "running"


def test_restore_keeps_running_while_another_owner_holds_the_lease(
    tmp_path: Path,
) -> None:
    path = tmp_path / "db.sqlite3"
    _running_control_run(path)
    with ProviderLease(path):
        service = RefreshService(path, None)
        service.restore()
        assert service.snapshot().status == "running"
        assert service.snapshot().stage == "recovery_required"


def test_recover_marks_stale_run_interrupted_and_unblocks_claims(
    tmp_path: Path,
) -> None:
    path = tmp_path / "db.sqlite3"
    run_id = _running_control_run(path)
    with pytest.raises(RefreshConflict) as blocked:
        claim_refresh(path)
    assert blocked.value.status_code == 409
    assert recover_interrupted_runs(path) == {"runs": 1, "attempts": 0}
    status, finished_at, category = _run_row(path, run_id)
    assert status == "interrupted"
    assert finished_at is not None
    assert category is None
    assert recover_interrupted_runs(path) == {"runs": 0, "attempts": 0}
    with pytest.raises(RefreshConflict) as cooling:
        claim_refresh(path)
    # The recovered run now counts as finished: the confirmable interval,
    # not the running conflict.
    assert cooling.value.status_code == 409
    assert cooling.value.recent_finished_at == finished_at


def test_recover_refuses_while_provider_lease_is_held(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    run_id = _running_control_run(path)
    with ProviderLease(path), pytest.raises(ProviderBusy):
        recover_interrupted_runs(path)
    assert _run_row(path, run_id)[0] == "running"


def test_recover_cli_prints_only_counts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "db.sqlite3"
    _running_control_run(path)
    assert refresh.main(["recover", str(path)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "attempts_interrupted": 0,
        "runs_interrupted": 1,
    }


def test_recover_cli_reports_busy_without_changes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "db.sqlite3"
    run_id = _running_control_run(path)
    with ProviderLease(path):
        assert refresh.main(["recover", str(path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "busy" in captured.err.lower()
    assert _run_row(path, run_id)[0] == "running"


def test_finish_records_interrupted_status(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    run_id = _running_control_run(path)
    service = RefreshService(path, None)
    service._finish(run_id, False, None, interrupted=True)
    status, finished_at, category = _run_row(path, run_id)
    assert (status, category) == ("interrupted", None)
    assert finished_at is not None
    snapshot = service.snapshot()
    assert snapshot.status == "interrupted"
    assert snapshot.stage == "stopped"
    assert any("interrupted" in log.message for log in snapshot.logs)


def test_request_stop_sets_flag_and_joins_worker_within_bound(tmp_path: Path) -> None:
    service = RefreshService(tmp_path / "db.sqlite3", None)
    assert not service.stop_requested()
    seen: list[bool] = []

    def worker() -> None:
        seen.append(service.stop_event.wait(5))

    service.worker = threading.Thread(target=worker)
    service.worker.start()
    started = time.monotonic()
    service.request_stop(timeout=5)
    assert time.monotonic() - started < 5
    assert service.stop_requested()
    assert seen == [True]
    assert not service.worker.is_alive()


def test_detail_pass_stops_between_postings(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[str] = []

    def start(connection: object, run: str, posting: str) -> str:
        started.append(posting)
        return posting

    def content(
        attempt_id: str, posting: StoredListing
    ) -> tuple[StoredPostingContent, None]:
        return cast(StoredPostingContent, SimpleNamespace(id=posting.id)), None

    monkeypatch.setattr(detail_fetch, "start_content_attempt", start)
    monkeypatch.setattr(
        detail_fetch, "complete_content_attempt", lambda *args, **kwargs: 1
    )
    postings = [
        cast(StoredListing, SimpleNamespace(id=f"control-{index}")) for index in (0, 1)
    ]
    collection = DetailCollection(
        DetailSource((), {}, (), lambda: True),
        cast(sqlite3.Connection, None),
        "control-run",
        postings,
        lambda progress: None,
        should_stop=lambda: bool(started),
        gate=RequestGate(PacingConfig(), daily_remaining=10),
    )
    collection._content = content  # type: ignore[method-assign]
    with pytest.raises(CollectionInterrupted):
        collection.run()
    assert started == ["control-0"]


def test_listing_pass_stops_between_pages() -> None:
    fetched: list[int] = []
    source = ListingSource(
        form=[],
        headers={},
        first_page=ListingPage.model_validate(
            {"totalResults": 0, "page": 1, "data": []}
        ),
        page_size=50,
        finished_at=time.monotonic(),
        first_duration=None,
    )
    source.fetch = lambda number: fetched.append(number)  # type: ignore[method-assign,assignment,return-value]
    collection = ListingCollection(source, should_stop=lambda: True)
    with pytest.raises(CollectionInterrupted):
        collection.run()
    assert fetched == []
    assert collection.pages_completed == 0


def test_api_shutdown_requests_cooperative_refresh_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[float | None] = []
    monkeypatch.setattr(
        RefreshService,
        "request_stop",
        lambda self, timeout=None: calls.append(timeout),
    )
    path = tmp_path / "db.sqlite3"
    with TestClient(create_app(path), base_url="https://localhost"):
        assert calls == []
    assert len(calls) == 1
