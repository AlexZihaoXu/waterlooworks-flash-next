"""Atomic content-only checkpoints; legacy v3 metadata tables stay untouched.

Formatting (schema v21) is written in the same transaction into its own
table, bound to the content hash; it never changes the saved text, the hash
or the content revisions."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

from waterlooworks_flash_next.compact_history import HistoryReader, store_payload
from waterlooworks_flash_next.content import (
    StoredPostingContent,
    StoredPostingFormatting,
)
from waterlooworks_flash_next.database import DatabaseError, canonical_snapshot
from waterlooworks_flash_next.detail_schema import DETAIL_ERROR_CATEGORIES
from waterlooworks_flash_next.rich_text import FORMAT_VERSION, same_characters

EXTRACTOR_VERSION = "labels-text-v3"


@contextmanager
def _write(connection: sqlite3.Connection) -> Iterator[None]:
    if connection.in_transaction:
        raise DatabaseError("Content persistence requires its own transaction.")
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            yield
    except (sqlite3.Error, ValueError, TypeError, AttributeError):
        raise DatabaseError(
            "Content persistence failed; diagnostics suppressed."
        ) from None


def start_content_attempt(
    connection: sqlite3.Connection, run_id: str, posting_id: str
) -> str:
    with _write(connection):
        run = connection.execute(
            "SELECT status FROM collection_runs WHERE id=?", (run_id,)
        ).fetchone()
        if run is None or run[0] != "running":
            raise DatabaseError("Content fetch requires a running collection.")
        attempt_id = str(uuid4())
        connection.execute(
            "INSERT INTO posting_content_attempts"
            "(attempt_id,posting_id,run_id,started_at) VALUES(?,?,?,?)",
            (attempt_id, posting_id, run_id, datetime.now(UTC).isoformat()),
        )
        return attempt_id


def _pending(connection: sqlite3.Connection, attempt_id: str) -> tuple[str, str]:
    row = connection.execute(
        "SELECT posting_id,run_id,status FROM posting_content_attempts "
        "WHERE attempt_id=?",
        (attempt_id,),
    ).fetchone()
    if row is None or row[2] != "running":
        raise DatabaseError("Content attempt is missing or terminal.")
    return str(row[0]), str(row[1])


def record_content_request(
    connection: sqlite3.Connection, attempt_id: str, *, retry: bool
) -> None:
    """Persist request intent before network; crashes never silently replay it."""
    with _write(connection):
        _, run_id = _pending(connection, attempt_id)
        run = connection.execute(
            "SELECT status FROM collection_runs WHERE id=?", (run_id,)
        ).fetchone()
        if run is None or run[0] != "running":
            raise DatabaseError("Content requests require a running collection.")
        connection.execute(
            "UPDATE posting_content_attempts SET request_count=request_count+1,"
            "retry_count=retry_count+? WHERE attempt_id=?",
            (int(retry), attempt_id),
        )
        connection.execute(
            "UPDATE collection_runs SET detail_requests=detail_requests+1,"
            "detail_retries=detail_retries+? WHERE id=?",
            (int(retry), run_id),
        )


def fail_content_attempt(
    connection: sqlite3.Connection,
    attempt_id: str,
    category: str,
    *,
    http_status: int | None = None,
) -> None:
    with _write(connection):
        _pending(connection, attempt_id)
        if category not in DETAIL_ERROR_CATEGORIES:
            raise DatabaseError("Unsupported content failure category.")
        connection.execute(
            "UPDATE posting_content_attempts SET status='failed',finished_at=?,"
            "http_status=?,error_category=? WHERE attempt_id=?",
            (datetime.now(UTC).isoformat(), http_status, category, attempt_id),
        )


def interrupt_content_attempt(connection: sqlite3.Connection, attempt_id: str) -> None:
    """Close an attempt stopped cleanly by a pacing cap or shutdown (not failed)."""
    with _write(connection):
        _pending(connection, attempt_id)
        connection.execute(
            "UPDATE posting_content_attempts SET status='interrupted',finished_at=?,"
            "error_category='cancelled' WHERE attempt_id=?",
            (datetime.now(UTC).isoformat(), attempt_id),
        )


def complete_content_attempt(
    connection: sqlite3.Connection,
    attempt_id: str,
    content: StoredPostingContent,
    formatting: StoredPostingFormatting | None = None,
) -> int:
    """Save content (a new revision only when its hash changed) and formatting.

    Identical text keeps the content row, hash and revisions; only its
    sightings and, when supplied, the formatting row are updated.
    """
    with _write(connection):
        posting_id, run_id = _pending(connection, attempt_id)
        row = connection.execute(
            "SELECT status FROM collection_runs WHERE id=?", (run_id,)
        ).fetchone()
        if row is None or row[0] != "running":
            raise DatabaseError("Content completion requires a running collection.")
        validated = StoredPostingContent.model_validate(content.model_dump())
        if validated.id != posting_id:
            raise DatabaseError("Content identity does not match attempt.")
        text, digest = canonical_snapshot(validated.model_dump())
        now = datetime.now(UTC).isoformat()
        previous = connection.execute(
            "SELECT content_hash FROM posting_contents WHERE posting_id=?",
            (posting_id,),
        ).fetchone()
        latest = connection.execute(
            "SELECT payload_id,content_hash FROM posting_content_revisions "
            "WHERE posting_id=? ORDER BY revision_id DESC LIMIT 1",
            (posting_id,),
        ).fetchone()
        if previous is not None and (latest is None or latest[1] != previous[0]):
            raise DatabaseError("Current content revision identity mismatch.")
        if latest is not None:
            HistoryReader(connection).read(latest[0], latest[1], "content")
        if previous is None or previous[0] != digest:
            payload_id = store_payload(
                connection,
                "content",
                text,
                digest,
                latest[0] if latest is not None else None,
            )
            connection.execute(
                """INSERT INTO posting_contents
                    (posting_id,content_json,content_hash,extractor_version,
                     first_seen_at,last_seen_at,first_seen_run_id,last_seen_run_id)
                    VALUES(?,?,?,?,?,?,?,?)
                    ON CONFLICT(posting_id) DO UPDATE SET
                        content_json=excluded.content_json,
                        content_hash=excluded.content_hash,
                        extractor_version=excluded.extractor_version,
                        last_seen_at=excluded.last_seen_at,
                        last_seen_run_id=excluded.last_seen_run_id""",
                (posting_id, text, digest, EXTRACTOR_VERSION, now, now, run_id, run_id),
            )
            connection.execute(
                "INSERT INTO posting_content_revisions(posting_id,payload_id,"
                "content_hash,extractor_version,run_id,observed_at) "
                "VALUES(?,?,?,?,?,?)",
                (posting_id, payload_id, digest, EXTRACTOR_VERSION, run_id, now),
            )
        else:
            connection.execute(
                "UPDATE posting_contents SET last_seen_at=?,last_seen_run_id=? "
                "WHERE posting_id=?",
                (now, run_id, posting_id),
            )
        if formatting is not None:
            _save_formatting(connection, posting_id, digest, formatting, run_id, now)
        row = connection.execute(
            "SELECT revision_id FROM posting_content_revisions WHERE posting_id=? "
            "ORDER BY revision_id DESC LIMIT 1",
            (posting_id,),
        ).fetchone()
        if row is None:
            raise DatabaseError("Content revision missing.")
        revision_id = int(row[0])
        connection.execute(
            "UPDATE posting_content_attempts SET status='succeeded',finished_at=?,"
            "http_status=200,revision_id=? WHERE attempt_id=?",
            (now, revision_id, attempt_id),
        )
        connection.execute(
            "UPDATE collection_runs SET details_completed=details_completed+1 "
            "WHERE id=?",
            (run_id,),
        )
        return revision_id


def _save_formatting(
    connection: sqlite3.Connection,
    posting_id: str,
    digest: str,
    formatting: StoredPostingFormatting,
    run_id: str,
    now: str,
) -> None:
    """Caller owns the transaction; replaces this posting's current formatting."""
    validated = StoredPostingFormatting.model_validate_json(
        formatting.model_dump_json()
    )
    blocks = json.dumps(
        validated.model_dump(exclude_defaults=True)["fields"],
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    connection.execute(
        """INSERT INTO posting_content_formatting
            (posting_id,content_hash,format_version,blocks_json,captured_at,run_id)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(posting_id) DO UPDATE SET
                content_hash=excluded.content_hash,
                format_version=excluded.format_version,
                blocks_json=excluded.blocks_json,
                captured_at=excluded.captured_at,
                run_id=excluded.run_id""",
        (posting_id, digest, validated.version, blocks, now, run_id),
    )


def formatting_from_row(
    content: StoredPostingContent, blocks_json: str | None
) -> StoredPostingFormatting | None:
    """Validated formatting for labels whose text matches the saved content.

    None (plain text only) when nothing is stored, the row does not validate,
    or no label survives the same-characters check.
    """
    if blocks_json is None:
        return None
    try:
        stored = StoredPostingFormatting.model_validate_json(
            json.dumps({"version": FORMAT_VERSION, "fields": json.loads(blocks_json)})
        )
    except ValueError:
        return None
    kept = {
        label: blocks
        for label, blocks in stored.fields.items()
        if label in content.fields and same_characters(content.fields[label], blocks)
    }
    return StoredPostingFormatting(fields=kept) if kept else None


# Current content joined with formatting captured for that exact hash/version.
FORMATTING_JOIN = (
    "LEFT JOIN posting_content_formatting f ON f.posting_id=c.posting_id "
    "AND f.content_hash=c.content_hash AND f.format_version=?"
)


def read_formatting(
    connection: sqlite3.Connection, posting_id: str
) -> StoredPostingFormatting | None:
    row = connection.execute(
        "SELECT c.content_json,f.blocks_json FROM posting_contents c "
        f"{FORMATTING_JOIN} WHERE c.posting_id=?",
        (FORMAT_VERSION, posting_id),
    ).fetchone()
    if row is None:
        return None
    return formatting_from_row(StoredPostingContent.model_validate_json(row[0]), row[1])
