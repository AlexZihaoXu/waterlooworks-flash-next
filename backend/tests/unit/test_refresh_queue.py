"""Detail queue selection with primitive control hashes and a real empty database.

No posting records are inserted; selection decisions are exercised on control
hash strings and the SQL is exercised against real empty migrations.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import cast

from waterlooworks_flash_next.database import StoredListing, open_database
from waterlooworks_flash_next.refresh import (
    FAILED_SAME_REVISION,
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
    finally:
        connection.close()
