"""Detail queue selection with primitive control hashes and a real empty database.

No posting records are inserted; selection decisions are exercised on control
hash strings and the SQL is exercised against real empty migrations.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from waterlooworks_flash_next.database import StoredListing, open_database
from waterlooworks_flash_next.refresh import (
    FAILED_SAME_REVISION,
    UNCHANGED,
    configured_full_refetch,
    detail_skip_reason,
    select_detail_queue,
)

CURRENT = "a" * 64
OLDER = "b" * 64


def test_item_failure_on_same_listing_revision_is_skipped() -> None:
    for category in ("response_schema", "response_size", "response_encoding"):
        assert (
            detail_skip_reason(CURRENT, ("failed", category, CURRENT))
            == FAILED_SAME_REVISION
        )


def test_failure_on_older_revision_or_session_failure_is_retried() -> None:
    assert detail_skip_reason(CURRENT, None) is None
    assert detail_skip_reason(CURRENT, ("failed", "response_schema", OLDER)) is None
    assert detail_skip_reason(CURRENT, ("failed", "response_schema", None)) is None
    for category in ("authentication", "rate_limit", "timeout", "internal"):
        assert detail_skip_reason(CURRENT, ("failed", category, CURRENT)) is None
    assert detail_skip_reason(CURRENT, ("interrupted", "cancelled", CURRENT)) is None


def test_queue_selection_runs_on_real_empty_schema(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "db.sqlite3")
    try:
        assert select_detail_queue(connection, []) == ([], {})
        # Identity-only sentinels absent from the database are always queued.
        unknown = [
            cast(StoredListing, SimpleNamespace(id="1")),
            cast(StoredListing, SimpleNamespace(id="2")),
        ]
        assert select_detail_queue(connection, unknown) == (unknown, {})
        assert select_detail_queue(connection, unknown, full_refetch=True) == (
            unknown,
            {},
        )
    finally:
        connection.close()


def test_unchanged_listing_with_current_content_is_skipped() -> None:
    assert (
        detail_skip_reason(
            CURRENT,
            ("succeeded", None, CURRENT),
            success_hash=CURRENT,
            content_current=True,
        )
        == UNCHANGED
    )
    # A later session-level failure does not invalidate the saved content.
    assert (
        detail_skip_reason(
            CURRENT,
            ("failed", "timeout", CURRENT),
            success_hash=CURRENT,
            content_current=True,
        )
        == UNCHANGED
    )


def test_changed_listing_or_outdated_extraction_is_fetched() -> None:
    assert (
        detail_skip_reason(
            CURRENT,
            ("succeeded", None, OLDER),
            success_hash=OLDER,
            content_current=True,
        )
        is None
    )
    assert (
        detail_skip_reason(
            CURRENT,
            ("succeeded", None, CURRENT),
            success_hash=CURRENT,
            content_current=False,
        )
        is None
    )


def test_full_refetch_bypasses_every_skip() -> None:
    assert (
        detail_skip_reason(
            CURRENT,
            ("succeeded", None, CURRENT),
            success_hash=CURRENT,
            content_current=True,
            full_refetch=True,
        )
        is None
    )
    assert (
        detail_skip_reason(
            CURRENT, ("failed", "response_schema", CURRENT), full_refetch=True
        )
        is None
    )


def test_full_refetch_configuration_is_strict() -> None:
    assert configured_full_refetch(None) is False
    assert configured_full_refetch("0") is False
    assert configured_full_refetch("1") is True
    for value in ("true", "yes", "2", " 1", "maybe-secret"):
        with pytest.raises(ValueError, match="WW_DETAIL_FULL_REFETCH") as refused:
            configured_full_refetch(value)
        # Validation errors never echo the supplied value.
        assert value.strip() not in str(refused.value).replace("0 or 1", "")


def test_service_carries_server_owned_full_refetch(tmp_path: Path) -> None:
    from waterlooworks_flash_next.refresh import RefreshService

    assert RefreshService(tmp_path / "db.sqlite3", None).full_refetch is False
    assert (
        RefreshService(tmp_path / "db.sqlite3", None, full_refetch=True).full_refetch
        is True
    )
