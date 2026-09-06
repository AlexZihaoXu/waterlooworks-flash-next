"""Atomic detail-attempt lifecycle and independent revision history. No network I/O."""

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

from waterlooworks_flash_next.database import DatabaseError, canonical_snapshot
from waterlooworks_flash_next.detail_schema import DETAIL_ERROR_CATEGORIES
from waterlooworks_flash_next.details import StoredDetail


@contextmanager
def _write(connection: sqlite3.Connection) -> Iterator[None]:
    if connection.in_transaction:
        raise DatabaseError("Detail persistence requires its own transaction.")
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            yield
    except (sqlite3.Error, ValueError, TypeError, AttributeError):
        raise DatabaseError(
            "Detail persistence failed; diagnostics suppressed."
        ) from None


def start_detail_attempt(
    connection: sqlite3.Connection, run_id: str, posting_id: str
) -> str:
    """Record intent before fetching. Requires an existing posting and running run."""
    with _write(connection):
        if not re.fullmatch(r"[0-9]+", posting_id):
            raise DatabaseError("Invalid posting identity.")
        run = connection.execute(
            "SELECT status FROM collection_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if run is None or run[0] != "running":
            raise DatabaseError("Detail fetch requires a running collection.")
        attempt_id = str(uuid4())
        connection.execute(
            "INSERT INTO posting_fetch_attempts "
            "(attempt_id, posting_id, run_id, started_at) VALUES (?, ?, ?, ?)",
            (attempt_id, posting_id, run_id, datetime.now(UTC).isoformat()),
        )
        return attempt_id


def _pending(connection: sqlite3.Connection, attempt_id: str) -> tuple[str, str]:
    attempt = connection.execute(
        "SELECT posting_id, run_id, status FROM posting_fetch_attempts "
        "WHERE attempt_id = ?",
        (attempt_id,),
    ).fetchone()
    if attempt is None or attempt[2] != "running":
        raise DatabaseError("Detail attempt is missing or already terminal.")
    return str(attempt[0]), str(attempt[1])


def fail_detail_attempt(
    connection: sqlite3.Connection,
    attempt_id: str,
    error_category: str,
    *,
    http_status: int | None = None,
    interrupted: bool = False,
) -> None:
    """Finalize only the attempt. A failure never touches valid snapshots/history."""
    with _write(connection):
        _pending(connection, attempt_id)
        if error_category not in DETAIL_ERROR_CATEGORIES:
            raise DatabaseError("Unsupported detail failure category.")
        if http_status is not None and (
            type(http_status) is not int or not 100 <= http_status <= 599
        ):
            raise DatabaseError("Invalid HTTP status.")
        connection.execute(
            "UPDATE posting_fetch_attempts SET status = ?, finished_at = ?, "
            "http_status = ?, error_category = ? WHERE attempt_id = ?",
            (
                "interrupted" if interrupted else "failed",
                datetime.now(UTC).isoformat(),
                http_status,
                error_category,
                attempt_id,
            ),
        )


def complete_detail_attempt(
    connection: sqlite3.Connection,
    attempt_id: str,
    detail: StoredDetail,
    *,
    extractor_version: str,
    http_status: int = 200,
) -> int:
    """Save verified metadata and finalize success atomically; return revision ID.

    Unchanged content reuses the current revision. Changed content/reversions append.
    Extractor version is recorded for every successful observation but is not hashed.
    Reserved body-content fields cannot be populated by this metadata-only contract.
    """
    with _write(connection):
        posting_id, run_id = _pending(connection, attempt_id)
        run = connection.execute(
            "SELECT status FROM collection_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if run is None or run[0] != "running":
            raise DatabaseError("Detail completion requires a running collection.")
        if type(http_status) is not int or http_status != 200:
            raise DatabaseError("Detail completion requires HTTP 200.")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", extractor_version):
            raise DatabaseError("Invalid extractor version identifier.")
        validated = StoredDetail.model_validate(detail.model_dump())
        if validated.id != posting_id:
            raise DatabaseError("Detail identity does not match its fetch attempt.")
        text, digest = canonical_snapshot(validated.model_dump())
        observed_at = datetime.now(UTC).isoformat()
        previous = connection.execute(
            "SELECT content_hash FROM posting_details WHERE posting_id = ?",
            (posting_id,),
        ).fetchone()
        if previous is None or previous[0] != digest:
            connection.execute(
                """
                INSERT INTO posting_details (posting_id, detail_json, content_hash,
                    extractor_version, first_seen_at, last_seen_at,
                    first_seen_run_id, last_seen_run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(posting_id) DO UPDATE SET
                    detail_json = excluded.detail_json,
                    content_hash = excluded.content_hash,
                    extractor_version = excluded.extractor_version,
                    last_seen_at = excluded.last_seen_at,
                    last_seen_run_id = excluded.last_seen_run_id
            """,
                (
                    posting_id,
                    text,
                    digest,
                    extractor_version,
                    observed_at,
                    observed_at,
                    run_id,
                    run_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO posting_detail_revisions
                    (posting_id, detail_json, content_hash,
                     extractor_version, run_id, observed_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (posting_id, text, digest, extractor_version, run_id, observed_at),
            )
        else:
            connection.execute(
                "UPDATE posting_details SET last_seen_at = ?, last_seen_run_id = ? "
                "WHERE posting_id = ?",
                (observed_at, run_id, posting_id),
            )
        revision = connection.execute(
            "SELECT revision_id FROM posting_detail_revisions "
            "WHERE posting_id = ? ORDER BY revision_id DESC LIMIT 1",
            (posting_id,),
        ).fetchone()
        if revision is None:
            raise DatabaseError("Current detail revision is missing.")
        revision_id = int(revision[0])
        connection.execute(
            "UPDATE posting_fetch_attempts SET status = 'succeeded', finished_at = ?, "
            "http_status = ?, revision_id = ?, extractor_version = ? "
            "WHERE attempt_id = ?",
            (observed_at, http_status, revision_id, extractor_version, attempt_id),
        )
        return revision_id
