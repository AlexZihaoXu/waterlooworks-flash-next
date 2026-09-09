"""Saved arrangement primitives and an empty read-only query contract."""

import sqlite3

from waterlooworks_flash_next.work_arrangement import (
    arrangement_value,
    read_work_arrangements,
)


def test_only_explicit_arrangements_are_displayed() -> None:
    for value in ("Remote", "Hybrid", "In-person"):
        assert arrangement_value(value) == value
        assert arrangement_value(f" {value} ") == value
    for invalid in (None, "", "Unknown", "Remote or hybrid", "Not remote", 1):
        assert arrangement_value(invalid) is None


def test_empty_current_content_query_does_not_write() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE posting_contents(posting_id TEXT, content_json TEXT)"
    )
    connection.execute("PRAGMA query_only=ON")
    assert read_work_arrangements(connection) == {}
    connection.close()
