"""Retry-After clamping/persistence, operator clearing and proxy-free openers."""

import sqlite3
from datetime import UTC, datetime, timedelta
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

import pytest

from waterlooworks_flash_next.database import open_database, start_collection_run
from waterlooworks_flash_next.detail_fetch import DetailFetchError, record_blocked_until
from waterlooworks_flash_next.listings import (
    MAX_RETRY_AFTER_SECONDS,
    FailureCategory,
    ListingFetchError,
    http_failure_from_error,
    retry_after_seconds,
)
from waterlooworks_flash_next.rate_limit import clear_blocks, main
from waterlooworks_flash_next.refresh import (
    RefreshConflict,
    RefreshService,
    claim_refresh,
)


def http_error(status: int, retry_after: str | None) -> HTTPError:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return HTTPError("https://example.invalid/", status, "status", headers, None)


def test_retry_after_is_clamped_to_one_day() -> None:
    assert MAX_RETRY_AFTER_SECONDS == 24 * 60 * 60
    assert retry_after_seconds("60", now=0) == 60
    assert retry_after_seconds("86400", now=0) == 86_400
    assert retry_after_seconds("86401", now=0) == 86_400
    assert retry_after_seconds("2147483647", now=0) == 86_400
    assert retry_after_seconds("99999999999999999999", now=0) == 86_400
    assert retry_after_seconds("Fri, 01 Jan 2100 00:00:00 GMT", now=0) == 86_400
    assert retry_after_seconds("invalid", now=0) is None
    assert retry_after_seconds("-1", now=0) is None
    assert retry_after_seconds(None, now=0) is None


def test_listing_rate_limit_keeps_a_clamped_retry_after() -> None:
    error = http_failure_from_error(http_error(429, "120"))
    assert error.category is FailureCategory.RATE_LIMIT
    assert error.status == 429
    assert error.retry_after == 120
    assert str(error) == "Listing HTTP 429."
    assert http_failure_from_error(http_error(429, "9999999")).retry_after == 86_400
    assert http_failure_from_error(http_error(429, None)).retry_after is None
    assert http_failure_from_error(http_error(503, "30")).retryable


def test_detail_errors_share_the_listing_retry_after_field() -> None:
    assert issubclass(DetailFetchError, ListingFetchError)
    error = DetailFetchError(FailureCategory.RATE_LIMIT, status=429, retry_after=7)
    assert error.retry_after == 7


def create_run(path: Path) -> str:
    connection = open_database(path)
    try:
        return start_collection_run(connection)
    finally:
        connection.close()


def test_block_helper_persists_for_a_run_and_blocks_claims(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    run = create_run(path)
    connection = open_database(path)
    record_blocked_until(connection, run, 3_600)
    blocked = connection.execute(
        "SELECT blocked_until FROM collection_runs WHERE id=?", (run,)
    ).fetchone()[0]
    connection.close()
    remaining = (datetime.fromisoformat(blocked) - datetime.now(UTC)).total_seconds()
    assert 3_590 <= remaining <= 3_600
    with sqlite3.connect(path) as raw:
        raw.execute(
            "UPDATE collection_runs SET status='failed', finished_at=?", (blocked,)
        )
    with pytest.raises(RefreshConflict) as refusal:
        claim_refresh(path)
    assert refusal.value.status_code == 429
    assert refusal.value.retry_after > 3_500


def test_block_helper_never_exceeds_the_ceiling(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    run = create_run(path)
    connection = open_database(path)
    record_blocked_until(connection, run, 10**9)
    blocked = connection.execute("SELECT blocked_until FROM collection_runs").fetchone()
    connection.close()
    ceiling = datetime.now(UTC) + timedelta(seconds=MAX_RETRY_AFTER_SECONDS + 5)
    assert datetime.fromisoformat(blocked[0]) <= ceiling


def test_refresh_service_persists_listing_phase_rate_limit(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    run = create_run(path)
    service = RefreshService(path, None)
    other = ListingFetchError("x", FailureCategory.HTTP, status=500)
    other.retry_after = 99
    for error in (other, ListingFetchError("x", FailureCategory.RATE_LIMIT)):
        service.persist_listing_block(run, error)
    with sqlite3.connect(path) as raw:
        assert raw.execute("SELECT blocked_until FROM collection_runs").fetchone() == (
            None,
        )
    limited = ListingFetchError("x", FailureCategory.RATE_LIMIT, status=429)
    limited.retry_after = 1_800
    service.persist_listing_block(run, limited)
    with sqlite3.connect(path) as raw:
        blocked = raw.execute("SELECT blocked_until FROM collection_runs").fetchone()[0]
    remaining = (datetime.fromisoformat(blocked) - datetime.now(UTC)).total_seconds()
    assert 1_790 <= remaining <= 1_800


def test_operator_can_clear_a_stale_block(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "db.sqlite3"
    run = create_run(path)
    far = datetime.now(UTC) + timedelta(days=365 * 60)
    with sqlite3.connect(path) as raw:
        raw.execute(
            "UPDATE collection_runs SET status='failed', finished_at=?, "
            "blocked_until=?",
            (datetime.now(UTC).isoformat(), far.isoformat()),
        )
    with pytest.raises(RefreshConflict):
        claim_refresh(path)
    assert clear_blocks(path) == 1
    assert clear_blocks(path) == 0
    with sqlite3.connect(path) as raw:
        assert raw.execute(
            "SELECT blocked_until, id FROM collection_runs"
        ).fetchone() == (None, run)
    # Only the normal post-run cooldown can remain.
    try:
        claim_refresh(path)
    except RefreshConflict as conflict:
        assert conflict.retry_after <= 300
    main(["clear", "--database", str(path)])
    assert "Cleared 0" in capsys.readouterr().out


def test_clear_cli_requires_an_explicit_existing_database(tmp_path: Path) -> None:
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(SystemExit):
        main(["clear", "--database", str(missing)])
    assert not missing.exists()
    with pytest.raises(SystemExit):
        main(["clear"])
