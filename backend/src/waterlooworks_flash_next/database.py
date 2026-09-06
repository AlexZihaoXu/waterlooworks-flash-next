"""SQLite schema v3 for listings, detail snapshots, attempts, and revision history.

No network calls, credential loading, or collection writes happen on import.
"""

import hashlib
import json
import os
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from waterlooworks_flash_next.detail_schema import migrate_details

SCHEMA_VERSION = 3


class DatabaseError(Exception):
    """Safe database errors; never include SQL parameters or private rows."""


class RunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class StoredListing(BaseModel):
    """Allowlisted listing snapshot; unknown field types remain JSON values.

    Use model_dump_json(exclude_unset=True) to preserve missing versus null fields.
    No raw action, apply control, cookie, or request fields are accepted.
    """

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    id: str = Field(pattern=r"^[0-9]+$")
    title: str | None = Field(default=None, repr=False)
    organization: str | None = Field(default=None, repr=False)
    division: JsonValue = Field(default=None, repr=False)
    openings: JsonValue = Field(default=None, repr=False)
    city: JsonValue = Field(default=None, repr=False)
    level: JsonValue = Field(default=None, repr=False)
    application_count: JsonValue = Field(default=None, repr=False)
    deadline: JsonValue = Field(default=None, repr=False)


_SCHEMA = (
    """
    CREATE TABLE collection_runs (
        id TEXT PRIMARY KEY NOT NULL,
        started_at TEXT NOT NULL CHECK (datetime(started_at) IS NOT NULL),
        finished_at TEXT
            CHECK (finished_at IS NULL OR datetime(finished_at) IS NOT NULL),
        status TEXT NOT NULL DEFAULT 'running'
            CHECK (status IN ('running', 'completed', 'failed', 'interrupted')),
        pages_completed INTEGER NOT NULL DEFAULT 0 CHECK (pages_completed >= 0),
        records_received INTEGER NOT NULL DEFAULT 0 CHECK (records_received >= 0),
        unique_postings INTEGER NOT NULL DEFAULT 0 CHECK (unique_postings >= 0),
        duplicates INTEGER NOT NULL DEFAULT 0 CHECK (duplicates >= 0),
        error_category TEXT CHECK (error_category IS NULL OR error_category IN (
            'authentication', 'rate_limit', 'redirect', 'http_status', 'timeout',
            'network', 'tls', 'incomplete_response', 'invalid_json',
            'response_encoding', 'response_schema', 'pagination',
            'response_size', 'stale_capture', 'internal'
        )),
        CHECK (records_received = unique_postings + duplicates),
        CHECK ((status = 'running' AND finished_at IS NULL) OR
               (status <> 'running' AND finished_at IS NOT NULL)),
        CHECK (finished_at IS NULL OR julianday(finished_at) >= julianday(started_at)),
        CHECK (error_category IS NULL OR status IN ('failed', 'interrupted'))
    ) STRICT
    """,
    """
    CREATE TABLE postings (
        posting_id TEXT PRIMARY KEY NOT NULL
            CHECK (posting_id <> '' AND posting_id NOT GLOB '*[^0-9]*'),
        listing_json TEXT NOT NULL
            CHECK (json_valid(listing_json))
            CHECK (json_type(listing_json) = 'object')
            CHECK (coalesce(json_type(listing_json, '$.id') = 'text' AND
                json_extract(listing_json, '$.id') = posting_id, 0))
            CHECK (json_remove(listing_json, '$.id', '$.title', '$.organization',
                '$.division', '$.openings', '$.city', '$.level',
                '$.application_count', '$.deadline') = '{}')
            CHECK (json_type(listing_json, '$.title') IS NULL OR
                json_type(listing_json, '$.title') IN ('text', 'null'))
            CHECK (json_type(listing_json, '$.organization') IS NULL OR
                json_type(listing_json, '$.organization') IN ('text', 'null')),
        title TEXT GENERATED ALWAYS AS (json_extract(listing_json, '$.title')) STORED,
        organization TEXT GENERATED ALWAYS AS (
            json_extract(listing_json, '$.organization')
        ) STORED,
        first_seen_at TEXT NOT NULL CHECK (datetime(first_seen_at) IS NOT NULL),
        last_seen_at TEXT NOT NULL CHECK (datetime(last_seen_at) IS NOT NULL),
        first_seen_run_id TEXT NOT NULL
            REFERENCES collection_runs(id) ON DELETE RESTRICT,
        last_seen_run_id TEXT NOT NULL
            REFERENCES collection_runs(id) ON DELETE RESTRICT,
        CHECK (julianday(last_seen_at) >= julianday(first_seen_at))
    ) STRICT
    """,
    "CREATE INDEX idx_runs_started ON collection_runs(started_at)",
    "CREATE INDEX idx_runs_status ON collection_runs(status)",
    "CREATE INDEX idx_postings_title ON postings(title COLLATE NOCASE)",
    "CREATE INDEX idx_postings_org ON postings(organization COLLATE NOCASE)",
    "CREATE INDEX idx_postings_last_seen ON postings(last_seen_at)",
    "CREATE INDEX idx_postings_first_run ON postings(first_seen_run_id)",
    "CREATE INDEX idx_postings_last_run ON postings(last_seen_run_id)",
)


def canonical_snapshot(payload: dict[str, JsonValue]) -> tuple[str, str]:
    """sha256-json-v1: sorted object keys, compact UTF-8 JSON, no NaN/Infinity.

    Array order, string contents, JSON types, and missing/null distinctions matter.
    Observation/run timestamps are not part of the listing payload.
    """
    try:
        text = json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        return text, hashlib.sha256(text.encode("utf-8")).hexdigest()
    except (ValueError, TypeError, UnicodeError):
        raise DatabaseError("Cannot produce canonical listing JSON.") from None


def _migrate_history(connection: sqlite3.Connection) -> None:
    """Run inside the initialization transaction; backfill only known v1 snapshots."""
    connection.execute("""
        ALTER TABLE postings ADD COLUMN content_hash TEXT
        CHECK (length(content_hash) = 64 AND content_hash NOT GLOB '*[^0-9a-f]*')
    """)
    connection.execute("""
        CREATE TABLE posting_revisions (
            revision_id INTEGER PRIMARY KEY,
            posting_id TEXT NOT NULL REFERENCES postings(posting_id) ON DELETE RESTRICT,
            content_hash TEXT NOT NULL CHECK (
                length(content_hash) = 64 AND content_hash NOT GLOB '*[^0-9a-f]*'),
            hash_version TEXT NOT NULL DEFAULT 'sha256-json-v1'
                CHECK (hash_version = 'sha256-json-v1'),
            listing_json TEXT NOT NULL CHECK (json_valid(listing_json)),
            run_id TEXT NOT NULL REFERENCES collection_runs(id) ON DELETE RESTRICT,
            observed_at TEXT NOT NULL CHECK (datetime(observed_at) IS NOT NULL),
            is_baseline INTEGER NOT NULL DEFAULT 0 CHECK (is_baseline IN (0, 1))
        ) STRICT
    """)
    connection.execute("""
        CREATE INDEX idx_revision_posting
        ON posting_revisions(posting_id, revision_id DESC)
    """)
    connection.execute("CREATE INDEX idx_revision_run ON posting_revisions(run_id)")
    connection.execute("""
        CREATE TRIGGER revision_matches_current BEFORE INSERT ON posting_revisions
        WHEN NOT EXISTS (SELECT 1 FROM postings WHERE posting_id = NEW.posting_id
            AND listing_json = NEW.listing_json AND content_hash = NEW.content_hash
            AND last_seen_run_id = NEW.run_id AND last_seen_at = NEW.observed_at)
        BEGIN SELECT RAISE(ABORT, 'Revision must match current snapshot'); END
    """)
    for operation in ("INSERT", "UPDATE"):
        connection.execute(f"""
            CREATE TRIGGER require_hash_{operation.lower()}
            BEFORE {operation} ON postings
            WHEN NEW.content_hash IS NULL
            BEGIN SELECT RAISE(ABORT, 'Snapshot hash required'); END
        """)
    for operation in ("UPDATE", "DELETE"):
        connection.execute(f"""
            CREATE TRIGGER immutable_revision_{operation.lower()}
            BEFORE {operation} ON posting_revisions
            BEGIN SELECT RAISE(ABORT, 'Revisions are append-only'); END
        """)
    # Materialize before writes; do not mutate a table while streaming its SELECT.
    existing = connection.execute(
        "SELECT posting_id, listing_json, last_seen_run_id, last_seen_at FROM postings"
    ).fetchall()
    for row in existing:
        listing = StoredListing.model_validate_json(row[1])
        text, digest = canonical_snapshot(listing.model_dump(exclude_unset=True))
        connection.execute(
            "UPDATE postings SET listing_json = ?, content_hash = ? "
            "WHERE posting_id = ?",
            (text, digest, row[0]),
        )
        connection.execute(
            """
            INSERT INTO posting_revisions
                (posting_id, content_hash, listing_json,
                 run_id, observed_at, is_baseline)
            VALUES (?, ?, ?, ?, ?, 1)
        """,
            (row[0], digest, text, row[2], row[3]),
        )


def save_listings(
    connection: sqlite3.Connection, run_id: str, listings: Sequence[StoredListing]
) -> int:
    """Atomically save a batch and return its number of new revisions.

    Owns its transaction; rejects an existing transaction rather than committing it.
    The run must be running. Counters/finalization remain the collector caller's job.
    Observed timestamps record persistence time. Never infer deletion from absence.
    """
    if connection.in_transaction:
        raise DatabaseError("Posting persistence requires its own transaction.")
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                "SELECT status FROM collection_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if run is None or run[0] != RunStatus.RUNNING:
                raise DatabaseError(
                    "Posting persistence requires a running collection."
                )
            observed_at = datetime.now(UTC).isoformat()
            changed = 0
            seen: set[str] = set()
            for supplied in listings:
                listing = StoredListing.model_validate(
                    supplied.model_dump(exclude_unset=True)
                )
                if listing.id in seen:
                    raise DatabaseError("Duplicate posting ID in persistence batch.")
                seen.add(listing.id)
                text, digest = canonical_snapshot(
                    listing.model_dump(exclude_unset=True)
                )
                previous = connection.execute(
                    "SELECT content_hash FROM postings WHERE posting_id = ?",
                    (listing.id,),
                ).fetchone()
                connection.execute(
                    """
                    INSERT INTO postings (posting_id, listing_json, content_hash,
                        first_seen_at, last_seen_at,
                        first_seen_run_id, last_seen_run_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(posting_id) DO UPDATE SET
                        listing_json = excluded.listing_json,
                        content_hash = excluded.content_hash,
                        last_seen_at = excluded.last_seen_at,
                        last_seen_run_id = excluded.last_seen_run_id
                """,
                    (
                        listing.id,
                        text,
                        digest,
                        observed_at,
                        observed_at,
                        run_id,
                        run_id,
                    ),
                )
                # Compare with current, not history: A -> B -> A is a change.
                if previous is None or previous[0] != digest:
                    connection.execute(
                        """
                        INSERT INTO posting_revisions
                            (posting_id, content_hash, listing_json,
                             run_id, observed_at)
                        VALUES (?, ?, ?, ?, ?)
                    """,
                        (listing.id, digest, text, run_id, observed_at),
                    )
                    changed += 1
            return changed
    except (sqlite3.Error, ValueError, TypeError):
        raise DatabaseError(
            "Posting persistence failed; diagnostics suppressed."
        ) from None


def open_database(path: Path) -> sqlite3.Connection:
    """Create/migrate explicitly, enabling foreign keys, WAL and a 5s busy timeout.

    SQLite >= 3.37 with JSON support is required. The caller owns connection.close().
    Use a private local path such as data/waterlooworks.sqlite3, never a network share.
    """
    connection: sqlite3.Connection | None = None
    try:
        if sqlite3.sqlite_version_info < (3, 37, 0):
            raise DatabaseError("SQLite 3.37 or newer is required.")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        connection = sqlite3.connect(path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise DatabaseError("Database schema is newer than this application.")
            if version == 0:
                tables = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
                if tables:
                    raise DatabaseError(
                        "Refusing to initialize an unversioned populated schema."
                    )
                for statement in _SCHEMA:
                    connection.execute(statement)
                connection.execute("PRAGMA user_version = 1")
                version = 1
            if version == 1:
                _migrate_history(connection)
                connection.execute("PRAGMA user_version = 2")
                version = 2
            if version == 2:
                migrate_details(connection)
                connection.execute("PRAGMA user_version = 3")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection
    except Exception as error:
        if connection is not None:
            connection.close()
        if isinstance(error, DatabaseError):
            raise
        raise DatabaseError(
            "Database initialization failed; diagnostics suppressed."
        ) from None


def start_collection_run(connection: sqlite3.Connection) -> str:
    """Create real empty run metadata; does not fetch data or start authentication."""
    if connection.in_transaction:
        raise DatabaseError("Run creation requires its own transaction.")
    run_id = str(uuid4())
    started_at = datetime.now(UTC).isoformat()
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO collection_runs (id, started_at, status) VALUES (?, ?, ?)",
                (run_id, started_at, RunStatus.RUNNING.value),
            )
        return run_id
    except sqlite3.Error:
        raise DatabaseError(
            "Could not start collection run; diagnostics suppressed."
        ) from None
