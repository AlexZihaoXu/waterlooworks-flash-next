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
    lead_with_visible,
    prefer_unsaved,
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


def test_capped_runs_progress_by_fetching_unsaved_postings_first() -> None:
    # Identity-only sentinels; no posting records.
    queue = [cast(StoredListing, SimpleNamespace(id=str(n))) for n in range(5)]
    ordered = prefer_unsaved(queue, {"0", "2"})
    assert [item.id for item in ordered] == ["1", "3", "4", "0", "2"]
    assert prefer_unsaved(queue, set()) == queue


def _ids(items: list[StoredListing]) -> list[str]:
    return [item.id for item in items]


def _sentinels(*ids: str) -> list[StoredListing]:
    # Identity/title-only sentinels; no posting records.
    return [cast(StoredListing, SimpleNamespace(id=i, title="t")) for i in ids]


def test_bootstrap_posting_is_queued_from_the_visible_first_page() -> None:
    # The title click needs a posting the browser's first board page shows.
    eligible = _sentinels("60", "61", "7", "62")
    queue, limited = lead_with_visible(eligible, eligible, ["5", "7", "8"], 3)
    assert _ids(queue) == ["7", "60", "61"]
    assert limited


def test_visible_posting_counts_inside_the_detail_limit() -> None:
    eligible = _sentinels(*(str(n) for n in range(60, 70)), "9")
    queue, limited = lead_with_visible(eligible, eligible, ["9"], 2)
    assert _ids(queue) == ["9", "60"]
    assert limited


def test_saved_visible_posting_leads_when_no_queued_one_is_visible() -> None:
    listings = _sentinels("1", "2", "60", "61")
    eligible = listings[2:]
    queue, limited = lead_with_visible(eligible, listings, ["1", "2"], 2)
    assert _ids(queue) == ["1", "60"]
    assert limited
    queue, limited = lead_with_visible(eligible, listings, ["1", "2"], None)
    assert _ids(queue) == ["1", "60", "61"]
    assert not limited


def test_untitled_visible_postings_cannot_lead() -> None:
    listings = [
        cast(StoredListing, SimpleNamespace(id="1", title=None)),
        *_sentinels("60"),
    ]
    queue, _ = lead_with_visible(listings[1:], listings, ["1"], None)
    assert _ids(queue) == ["60"]


def test_empty_queue_stays_empty() -> None:
    listings = _sentinels("1")
    assert lead_with_visible([], listings, ["1"], 5) == ([], False)
