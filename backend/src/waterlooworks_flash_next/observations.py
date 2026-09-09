"""Append-only listing observation evidence; no collection or public API actions."""

import sqlite3
from datetime import UTC, datetime

from waterlooworks_flash_next.compact_history import HistoryReader


def utc_timestamp(value: datetime) -> str:
    """Require an aware instant; never silently interpret local/naive time."""
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("Observation timestamp requires a timezone.")
    try:
        return value.astimezone(UTC).isoformat(timespec="microseconds")
    except (OverflowError, ValueError):
        raise ValueError("Observation timestamp is outside the UTC range.") from None


def migrate_observations(connection: sqlite3.Connection) -> None:
    """Inside the schema transaction; backfill only actual revision evidence."""
    connection.execute("""
        CREATE TABLE posting_observations (
            observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            posting_id TEXT NOT NULL REFERENCES postings(posting_id) ON DELETE RESTRICT,
            run_id TEXT NOT NULL REFERENCES collection_runs(id) ON DELETE RESTRICT,
            revision_id INTEGER NOT NULL
                REFERENCES posting_revisions(revision_id) ON DELETE RESTRICT,
            observed_at TEXT NOT NULL CHECK (datetime(observed_at) IS NOT NULL),
            timestamp_provenance TEXT NOT NULL CHECK (timestamp_provenance IN (
                'response_received', 'persistence_fallback',
                'legacy_persistence', 'legacy_baseline')),
            UNIQUE (posting_id, run_id),
            CHECK (timestamp_provenance IN ('legacy_persistence', 'legacy_baseline')
                OR (length(observed_at) = 32 AND substr(observed_at, 27) = '+00:00'
                    AND substr(observed_at, 20, 1) = '.'))
        ) STRICT
    """)
    connection.execute("""
        CREATE INDEX idx_observation_series
        ON posting_observations(posting_id, observation_id)
    """)
    connection.execute("""
        CREATE TRIGGER observation_matches_revision
        BEFORE INSERT ON posting_observations
        WHEN NOT EXISTS (SELECT 1 FROM posting_revisions
            WHERE revision_id = NEW.revision_id AND posting_id = NEW.posting_id)
        BEGIN SELECT RAISE(ABORT, 'Observation must reference posting evidence'); END
    """)
    connection.execute("""
        INSERT INTO posting_observations
            (posting_id, run_id, revision_id, observed_at, timestamp_provenance)
        SELECT posting_id, run_id, revision_id, observed_at,
            CASE is_baseline WHEN 1 THEN 'legacy_baseline'
                ELSE 'legacy_persistence' END
        FROM posting_revisions WHERE revision_id IN (
            SELECT max(revision_id) FROM posting_revisions GROUP BY posting_id, run_id)
        ORDER BY revision_id
    """)
    for operation in ("UPDATE", "DELETE"):
        connection.execute(f"""
            CREATE TRIGGER immutable_observation_{operation.lower()}
            BEFORE {operation} ON posting_observations
            BEGIN SELECT RAISE(ABORT, 'Observations are append-only'); END
        """)


def observation_series(
    connection: sqlite3.Connection, posting_id: str
) -> list[sqlite3.Row]:
    """Read append order with immutable raw evidence (missing != null).

    listing_json supplies application_count, openings and deadline with original
    JSON types; consumers must validate numeric projections, never default to zero.
    Legacy timestamps are original persistence strings, not response receipt times.
    """
    reader = HistoryReader(connection)
    rows = connection.execute(
        "SELECT o.*, r.content_hash, r.hash_version, r.is_baseline, r.payload_id "
        "FROM posting_observations o JOIN posting_revisions r USING(revision_id) "
        "WHERE o.posting_id = ? ORDER BY o.observation_id",
        (posting_id,),
    ).fetchall()
    # Preserve the existing sqlite3.Row/keyed evidence contract without SQL UDFs.
    result: list[sqlite3.Row] = []
    for row in rows:
        text = reader.read(row["payload_id"], row["content_hash"], "listing")
        cursor = connection.execute(
            "SELECT o.*, r.content_hash, r.hash_version, r.is_baseline, "
            "? AS listing_json "
            "FROM posting_observations o JOIN posting_revisions r USING(revision_id) "
            "WHERE o.observation_id=?",
            (text, row["observation_id"]),
        )
        result.append(cursor.fetchone())
    return result
