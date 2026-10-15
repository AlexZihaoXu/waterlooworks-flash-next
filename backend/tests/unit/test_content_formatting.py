"""Schema21 description formatting stored beside, never inside, saved content.

Structural checkpoints only: generic IDs and placeholder words written through
the production storage functions on real migrations. No network.
"""

import sqlite3
from pathlib import Path

import pytest

from waterlooworks_flash_next import database
from waterlooworks_flash_next.content import (
    StoredPostingContent,
    StoredPostingFormatting,
)
from waterlooworks_flash_next.content_storage import (
    EXTRACTOR_VERSION,
    complete_content_attempt,
    read_formatting,
    record_content_request,
    start_content_attempt,
)
from waterlooworks_flash_next.data_generation import EXCLUDED_TABLES, read_generation
from waterlooworks_flash_next.database import (
    StoredListing,
    canonical_snapshot,
    open_database,
    save_listings,
    start_collection_run,
)
from waterlooworks_flash_next.rich_text import FORMAT_VERSION

SUMMARY = "Intro bold words.\nSecond line."


def _content(summary: str = SUMMARY) -> StoredPostingContent:
    return StoredPostingContent(
        id="100001",
        fields={
            "Job Title:": "Title",
            "Organization:": "Organization",
            "Work Term:": "Term",
            "Job Summary:": summary,
        },
    )


def _formatting(word: str = "bold") -> StoredPostingFormatting:
    return StoredPostingFormatting.model_validate(
        {
            "fields": {
                "Job Summary:": [
                    {
                        "type": "paragraph",
                        "content": [
                            {"type": "text", "text": "Intro "},
                            {"type": "text", "text": word, "bold": True},
                            {"type": "text", "text": " words."},
                            {"type": "break"},
                            {"type": "text", "text": "Second line."},
                        ],
                    }
                ]
            }
        }
    )


def _run(connection: sqlite3.Connection) -> str:
    run_id = start_collection_run(connection)
    save_listings(connection, run_id, [StoredListing(id="100001", title="Title")])
    return run_id


def _capture(
    connection: sqlite3.Connection,
    run_id: str,
    content: StoredPostingContent,
    formatting: StoredPostingFormatting | None,
) -> int:
    attempt = start_content_attempt(connection, run_id, "100001")
    record_content_request(connection, attempt, retry=False)
    return complete_content_attempt(connection, attempt, content, formatting)


def _finish(connection: sqlite3.Connection, run_id: str) -> None:
    with connection:
        connection.execute(
            "UPDATE collection_runs SET status='completed',finished_at=? WHERE id=?",
            ("2999-01-01T00:00:00+00:00", run_id),
        )


def test_fresh_database_reaches_v21_with_an_empty_formatting_table(
    tmp_path: Path,
) -> None:
    connection = open_database(tmp_path / "fresh.sqlite3")
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 21
    assert database.SCHEMA_VERSION == 21
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(posting_content_formatting)")
    }
    assert columns == {
        "posting_id",
        "content_hash",
        "format_version",
        "blocks_json",
        "captured_at",
        "run_id",
    }
    # Formatting is not part of the plain-text content contract.
    content_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(posting_contents)")
    }
    assert not any("format" in column for column in content_columns)
    connection.close()


def test_v20_database_migrates_to_v21_preserving_saved_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "legacy.sqlite3"
    with monkeypatch.context() as pinned:
        pinned.setattr(database, "SCHEMA_VERSION", 20)
        connection = open_database(path)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 20
        run_id = _run(connection)
        attempt = start_content_attempt(connection, run_id, "100001")
        record_content_request(connection, attempt, retry=False)
        complete_content_attempt(connection, attempt, _content())
        before = tuple(connection.execute("SELECT * FROM posting_contents").fetchone())
        objects = {
            tuple(row)
            for row in connection.execute("SELECT type,name,sql FROM sqlite_master")
            if not str(row[1]).startswith("data_generation")
        }
        connection.close()
    connection = open_database(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 21
    assert objects <= {
        tuple(row)
        for row in connection.execute("SELECT type,name,sql FROM sqlite_master")
    }
    assert tuple(connection.execute("SELECT * FROM posting_contents").fetchone()) == (
        before
    )
    assert (
        connection.execute(
            "SELECT count(*) FROM posting_content_formatting"
        ).fetchone()[0]
        == 0
    )
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    connection.close()


def test_formatting_never_changes_the_content_hash_or_revisions(
    tmp_path: Path,
) -> None:
    plain = open_database(tmp_path / "plain.sqlite3")
    rich = open_database(tmp_path / "rich.sqlite3")
    try:
        _capture(plain, _run(plain), _content(), None)
        _capture(rich, _run(rich), _content(), _formatting())
        query = (
            "SELECT content_json,content_hash,extractor_version FROM posting_contents"
        )
        assert tuple(plain.execute(query).fetchone()) == tuple(
            rich.execute(query).fetchone()
        )
        digest = rich.execute("SELECT content_hash FROM posting_contents").fetchone()[0]
        assert digest == canonical_snapshot(_content().model_dump())[1]
        assert tuple(
            rich.execute(
                "SELECT content_hash,format_version FROM posting_content_formatting"
            ).fetchone()
        ) == (digest, FORMAT_VERSION)
        stored = rich.execute(
            "SELECT blocks_json FROM posting_content_formatting"
        ).fetchone()[0]
        assert "<" not in stored and "Intro" in stored
        assert EXTRACTOR_VERSION == "labels-text-v3"
    finally:
        plain.close()
        rich.close()


def test_backfill_of_identical_text_updates_only_the_formatting(
    tmp_path: Path,
) -> None:
    connection = open_database(tmp_path / "db.sqlite3")
    try:
        first = _run(connection)
        revision = _capture(connection, first, _content(), None)
        _finish(connection, first)
        content_row = connection.execute(
            "SELECT content_json,content_hash,extractor_version,first_seen_at "
            "FROM posting_contents"
        ).fetchone()
        assert read_formatting(connection, "100001") is None
        second = _run(connection)
        assert _capture(connection, second, _content(), _formatting()) == revision
        assert (
            connection.execute(
                "SELECT content_json,content_hash,extractor_version,first_seen_at "
                "FROM posting_contents"
            ).fetchone()
            == content_row
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM posting_content_revisions"
            ).fetchone()[0]
            == 1
        )
        assert read_formatting(connection, "100001") == _formatting()
    finally:
        connection.close()


def test_changed_text_is_a_normal_content_update_with_fresh_formatting(
    tmp_path: Path,
) -> None:
    connection = open_database(tmp_path / "db.sqlite3")
    try:
        first = _run(connection)
        _capture(connection, first, _content(), _formatting())
        _finish(connection, first)
        old_hash = connection.execute(
            "SELECT content_hash FROM posting_contents"
        ).fetchone()[0]
        second = _run(connection)
        changed = _content("Intro other words.\nSecond line.")
        _capture(connection, second, changed, _formatting("other"))
        new_hash = connection.execute(
            "SELECT content_hash FROM posting_contents"
        ).fetchone()[0]
        assert new_hash != old_hash
        assert (
            connection.execute(
                "SELECT count(*) FROM posting_content_revisions"
            ).fetchone()[0]
            == 2
        )
        assert tuple(
            connection.execute(
                "SELECT content_hash FROM posting_content_formatting"
            ).fetchone()
        ) == (new_hash,)
        assert read_formatting(connection, "100001") == _formatting("other")
    finally:
        connection.close()


def test_stale_or_mismatched_formatting_is_never_read(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "db.sqlite3")
    try:
        first = _run(connection)
        _capture(connection, first, _content(), _formatting())
        _finish(connection, first)
        second = _run(connection)
        # A content update without formatting leaves the old row stale.
        _capture(connection, second, _content("Other words.\nSecond line."), None)
        assert read_formatting(connection, "100001") is None
        assert read_formatting(connection, "999999") is None
    finally:
        connection.close()


def test_formatting_whose_text_disagrees_with_saved_content_is_withheld(
    tmp_path: Path,
) -> None:
    connection = open_database(tmp_path / "db.sqlite3")
    try:
        _capture(connection, _run(connection), _content(), _formatting("other"))
        assert read_formatting(connection, "100001") is None
    finally:
        connection.close()


def test_schema_binds_formatting_to_the_current_content_hash(
    tmp_path: Path,
) -> None:
    connection = open_database(tmp_path / "db.sqlite3")
    try:
        run_id = _run(connection)
        _capture(connection, run_id, _content(), None)
        digest = connection.execute(
            "SELECT content_hash FROM posting_contents"
        ).fetchone()[0]
        insert = (
            "INSERT INTO posting_content_formatting(posting_id,content_hash,"
            "format_version,blocks_json,captured_at,run_id) VALUES (?,?,?,?,?,?)"
        )
        valid = ("100001", digest, FORMAT_VERSION, "{}", "2026-01-01T00:00:00", run_id)

        def rejected(parameters: tuple[object, ...]) -> None:
            with pytest.raises(sqlite3.IntegrityError), connection:
                connection.execute(insert, parameters)

        rejected(("100001", "f" * 64, *valid[2:]))  # not the current content
        rejected(("999999", *valid[1:]))  # no saved content
        rejected((*valid[:3], "not json", *valid[4:]))
        rejected((*valid[:3], "[]", *valid[4:]))
        rejected((*valid[:2], "", *valid[3:]))
        rejected((*valid[:4], "not a time", run_id))
        with connection:
            connection.execute(insert, valid)
        rejected(valid)  # one current row per posting
    finally:
        connection.close()


def test_formatting_writes_refresh_shared_snapshots(tmp_path: Path) -> None:
    # Detail reads are uncached today; a future cached reader stays exact.
    assert "posting_content_formatting" not in EXCLUDED_TABLES
    connection = open_database(tmp_path / "db.sqlite3")
    try:
        run_id = _run(connection)
        _capture(connection, run_id, _content(), None)
        digest = connection.execute(
            "SELECT content_hash FROM posting_contents"
        ).fetchone()[0]
        before = read_generation(connection)
        with connection:
            connection.execute(
                "INSERT INTO posting_content_formatting(posting_id,content_hash,"
                "format_version,blocks_json,captured_at,run_id) VALUES (?,?,?,?,?,?)",
                ("100001", digest, FORMAT_VERSION, "{}", "2026-01-01T00:00:00", run_id),
            )
        assert read_generation(connection) != before
    finally:
        connection.close()
