"""Offline stage-order/configuration and durable rate-limit controls."""

import ast
import inspect
import sqlite3
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from waterlooworks_flash_next.content import StoredPostingContent
from waterlooworks_flash_next.database import (
    StoredListing,
    open_database,
    start_collection_run,
)
from waterlooworks_flash_next.detail_capture import (
    capture_detail_source,
    install_capture_guard,
)
from waterlooworks_flash_next.refresh import (
    RefreshConflict,
    RefreshService,
    claim_refresh,
    configured_detail_limit,
)


def test_refresh_saves_full_list_before_detail_collection() -> None:
    tree = ast.parse(textwrap.dedent(inspect.getsource(RefreshService._run)))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]

    def line(name: str) -> int:
        return next(n.lineno for n in calls if ast.unparse(n.func) == name)

    assert line("collection.run") < line("save_listings") < line("details.run")
    assert line("save_listings") < line("DetailCollection")
    capture = inspect.getsource(capture_detail_source)
    guard = inspect.getsource(install_capture_guard)
    assert "route.abort()" in guard
    assert "route.continue_" not in guard
    assert "route.fulfill(response=response)" in guard
    assert "max_redirects=0" in guard and "max_retries=0" in guard
    assert "if gate.invalid or gate.complete:" not in guard
    assert "page.unroute" not in capture and "build_opener" not in capture
    login = next(n for n in calls if ast.unparse(n.func) == "start_login")
    assert any(
        k.arg == "block_service_workers"
        and isinstance(k.value, ast.Constant)
        and k.value.value is True
        for k in login.keywords
    )


def test_server_owned_detail_cap_validation() -> None:
    assert configured_detail_limit(None) is None
    assert configured_detail_limit("12") == 12
    for value in ("", "0", "-1", "1.5", "100001", "value"):
        with pytest.raises(ValueError, match="WW_DETAIL_LIMIT"):
            configured_detail_limit(value)


def test_retry_after_blocks_manual_claim_after_standard_cooldown_and_survives_restore(
    tmp_path: Path,
) -> None:
    path = tmp_path / "db.sqlite3"
    connection = open_database(path)
    run = start_collection_run(connection)
    now = datetime.now(UTC)
    # Real empty control run; no posting records or requests.
    with connection:
        connection.execute(
            "UPDATE collection_runs SET started_at=?,status='failed',finished_at=?,"
            "blocked_until=? WHERE id=?",
            (
                (now - timedelta(minutes=20)).isoformat(),
                (now - timedelta(minutes=10)).isoformat(),
                (now + timedelta(seconds=60)).isoformat(),
                run,
            ),
        )
    connection.close()
    with pytest.raises(RefreshConflict) as refusal:
        claim_refresh(path)
    assert refusal.value.status_code == 429
    assert 55 <= refusal.value.retry_after <= 60
    service = RefreshService(path, None)
    service.restore()
    assert service.worker is None
    assert service.snapshot().status == "failed"
    assert 55 <= service.snapshot().retry_after <= 60


def test_bootstrap_and_remaining_operations_share_intent_and_burst_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from waterlooworks_flash_next import detail_fetch

    events: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        detail_fetch,
        "record_content_request",
        lambda connection, attempt, retry: events.append(("intent", retry)),
    )
    collection = detail_fetch.DetailCollection(
        None, cast(sqlite3.Connection, None), "control", [], lambda p: None
    )
    collection.pacer.jitter = lambda low, high: low

    # Primitive operations only, not posting fixtures or HTTP responses.
    def operation() -> str:
        assert events[-1] == ("intent", False)
        events.append(("network", False))
        return ""

    for _ in range(10):
        collection._operation("control", operation)
    assert collection.pacer.attempts == 10
    assert collection.pacer.bursts == 1
    assert len(events) == 20
    assert collection.pacer.wait_seconds(__import__("time").monotonic()) > 0


def test_first_content_reuse_never_calls_transport_or_counts_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from waterlooworks_flash_next import detail_fetch

    # Identity-only control sentinel: no fabricated content or platform response.
    sentinel = SimpleNamespace(id="control")
    source = detail_fetch.DetailSource((), {}, (), lambda: True)
    source.first_content = cast(StoredPostingContent, sentinel)
    collection = detail_fetch.DetailCollection(
        source, cast(sqlite3.Connection, None), "control", [], lambda p: None
    )
    monkeypatch.setattr(
        collection, "_request", lambda *args: pytest.fail("duplicate HTTP")
    )
    assert collection._content("control", cast(StoredListing, sentinel)) is cast(
        StoredPostingContent, sentinel
    )
    assert source.first_content is None
    assert collection.pacer.attempts == 0


def test_bootstrap_failure_is_inside_attempt_and_browser_cleanup_boundary() -> None:
    from waterlooworks_flash_next.detail_fetch import DetailCollection

    run = inspect.getsource(DetailCollection.run)
    assert run.index("start_content_attempt") < run.index("self._content")
    assert run.index("self._content") < run.index("fail_content_attempt")
    tree = ast.parse(textwrap.dedent(inspect.getsource(RefreshService._run)))
    bootstrap = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "bootstrap"
    )
    boundary = next(n for n in ast.walk(bootstrap) if isinstance(n, ast.Try))
    assert "capture_detail_source" in ast.unparse(boundary.body[0])
    cleanup = "\n".join(ast.unparse(node) for node in boundary.finalbody)
    assert "session.close()" in cleanup
    assert "sys.exception() is not None" in cleanup
