"""Transactional SQLite v3 migration; no fetched data is seeded or invented."""

import sqlite3

DETAIL_ERROR_CATEGORIES = (
    "authentication",
    "rate_limit",
    "redirect",
    "http_status",
    "timeout",
    "network",
    "tls",
    "incomplete_response",
    "invalid_json",
    "response_encoding",
    "response_schema",
    "pagination",
    "response_size",
    "stale_capture",
    "internal",
    "cancelled",
)


# Freeze v3's contract: future model changes require another migration.
_METADATA_TYPES = {
    "title": "text",
    "organization": "text",
    "division": "text",
    "division_id": "integer",
    "status": "text",
    "internal_status": "text",
    "has_prescreening": "true', 'false",
}
_RESERVED_CONTENT = (
    "work_term",
    "duration",
    "description",
    "responsibilities",
    "qualifications",
    "compensation_text",
    "work_arrangement",
    "location_details",
    "application_instructions",
)


def migrate_details(connection: sqlite3.Connection) -> None:
    """Caller owns the transaction; existing listings/history are untouched."""
    # All SQL interpolation below uses developer-owned constants, not runtime input.
    metadata_paths = ", ".join(f"'$.{key}'" for key in _METADATA_TYPES)
    content_paths = ", ".join(f"'$.{key}'" for key in _RESERVED_CONTENT)
    required = " AND ".join(
        f"json_type(detail_json, '$.metadata.{key}') IN ('{kind}')"
        for key, kind in _METADATA_TYPES.items()
    )
    nulls = " AND ".join(
        f"json_type(detail_json, '$.content.{key}') = 'null'"
        for key in _RESERVED_CONTENT
    )
    connection.execute(f"""
        CREATE TABLE posting_details (
            posting_id TEXT PRIMARY KEY NOT NULL
                REFERENCES postings(posting_id) ON DELETE RESTRICT,
            detail_json TEXT NOT NULL CHECK (json_valid(detail_json))
                CHECK (coalesce(json_type(detail_json) = 'object' AND
                    json_type(detail_json, '$.id') = 'text' AND
                    json_extract(detail_json, '$.id') = posting_id AND
                    json_extract(detail_json, '$.coverage') = 'metadata_only' AND
                    json_type(detail_json, '$.metadata') = 'object' AND
                    json_type(detail_json, '$.content') = 'object', 0))
                CHECK (json_remove(detail_json, '$.id', '$.metadata',
                    '$.content', '$.coverage') = '{{}}')
                CHECK (json_remove(json_extract(detail_json, '$.metadata'),
                    {metadata_paths}) = '{{}}')
                CHECK (json_remove(json_extract(detail_json, '$.content'),
                    {content_paths}) = '{{}}')
                CHECK (coalesce({required}, 0))
                CHECK (coalesce({nulls}, 0)),
            content_hash TEXT NOT NULL CHECK (length(content_hash) = 64
                AND content_hash NOT GLOB '*[^0-9a-f]*'),
            hash_version TEXT NOT NULL DEFAULT 'sha256-json-v1'
                CHECK (hash_version = 'sha256-json-v1'),
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            extractor_version TEXT NOT NULL
                CHECK (length(extractor_version) BETWEEN 1 AND 64
                AND extractor_version NOT GLOB '*[^A-Za-z0-9._-]*'),
            first_seen_at TEXT NOT NULL CHECK (datetime(first_seen_at) IS NOT NULL),
            last_seen_at TEXT NOT NULL CHECK (datetime(last_seen_at) IS NOT NULL),
            first_seen_run_id TEXT NOT NULL REFERENCES collection_runs(id),
            last_seen_run_id TEXT NOT NULL REFERENCES collection_runs(id),
            CHECK (julianday(last_seen_at) >= julianday(first_seen_at))
        ) STRICT
    """)
    connection.execute("""
        CREATE TABLE posting_detail_revisions (
            revision_id INTEGER PRIMARY KEY,
            posting_id TEXT NOT NULL REFERENCES posting_details(posting_id),
            detail_json TEXT NOT NULL CHECK (json_valid(detail_json)),
            content_hash TEXT NOT NULL CHECK (length(content_hash) = 64
                AND content_hash NOT GLOB '*[^0-9a-f]*'),
            hash_version TEXT NOT NULL DEFAULT 'sha256-json-v1'
                CHECK (hash_version = 'sha256-json-v1'),
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            extractor_version TEXT NOT NULL,
            run_id TEXT NOT NULL REFERENCES collection_runs(id),
            observed_at TEXT NOT NULL CHECK (datetime(observed_at) IS NOT NULL),
            UNIQUE (revision_id, posting_id)
        ) STRICT
    """)
    categories = ", ".join(f"'{value}'" for value in DETAIL_ERROR_CATEGORIES)
    connection.execute(f"""
        CREATE TABLE posting_fetch_attempts (
            attempt_id TEXT PRIMARY KEY NOT NULL,
            posting_id TEXT NOT NULL REFERENCES postings(posting_id),
            run_id TEXT NOT NULL REFERENCES collection_runs(id),
            started_at TEXT NOT NULL CHECK (datetime(started_at) IS NOT NULL),
            finished_at TEXT CHECK (finished_at IS NULL OR
                datetime(finished_at) IS NOT NULL),
            status TEXT NOT NULL DEFAULT 'running'
                CHECK (status IN ('running', 'succeeded', 'failed', 'interrupted')),
            http_status INTEGER CHECK (http_status BETWEEN 100 AND 599),
            error_category TEXT CHECK (error_category IN ({categories})),
            revision_id INTEGER,
            extractor_version TEXT CHECK (length(extractor_version) BETWEEN 1 AND 64
                AND extractor_version NOT GLOB '*[^A-Za-z0-9._-]*'),
            FOREIGN KEY (revision_id, posting_id)
                REFERENCES posting_detail_revisions(revision_id, posting_id),
            CHECK ((status = 'running' AND finished_at IS NULL
                    AND http_status IS NULL AND error_category IS NULL
                    AND revision_id IS NULL AND extractor_version IS NULL) OR
                   (status = 'succeeded' AND finished_at IS NOT NULL
                    AND http_status IS NOT NULL AND http_status = 200
                    AND error_category IS NULL AND revision_id IS NOT NULL
                    AND extractor_version IS NOT NULL) OR
                   (status IN ('failed', 'interrupted') AND finished_at IS NOT NULL
                    AND error_category IS NOT NULL AND revision_id IS NULL
                    AND extractor_version IS NULL)),
            CHECK (finished_at IS NULL OR
                julianday(finished_at) >= julianday(started_at))
        ) STRICT
    """)
    connection.execute("""
        CREATE TRIGGER detail_revision_matches_current
        BEFORE INSERT ON posting_detail_revisions
        WHEN NOT EXISTS (SELECT 1 FROM posting_details
            WHERE posting_id = NEW.posting_id AND detail_json = NEW.detail_json
                AND content_hash = NEW.content_hash AND hash_version = NEW.hash_version
                AND schema_version = NEW.schema_version
                AND extractor_version = NEW.extractor_version
                AND last_seen_run_id = NEW.run_id AND last_seen_at = NEW.observed_at)
        BEGIN SELECT RAISE(ABORT, 'Detail revision must match current'); END
    """)
    for operation in ("UPDATE", "DELETE"):
        connection.execute(f"""
            CREATE TRIGGER immutable_detail_revision_{operation.lower()}
            BEFORE {operation} ON posting_detail_revisions
            BEGIN SELECT RAISE(ABORT, 'Detail history is append-only'); END
        """)
    connection.execute("""
        CREATE TRIGGER terminal_attempt_immutable
        BEFORE UPDATE ON posting_fetch_attempts
        WHEN OLD.status <> 'running' OR NEW.attempt_id <> OLD.attempt_id
            OR NEW.posting_id <> OLD.posting_id OR NEW.run_id <> OLD.run_id
            OR NEW.started_at <> OLD.started_at
        BEGIN SELECT RAISE(ABORT, 'Attempt identity/terminal state is immutable'); END
    """)
    connection.execute("""
        CREATE TRIGGER attempt_no_delete BEFORE DELETE ON posting_fetch_attempts
        BEGIN SELECT RAISE(ABORT, 'Attempt history cannot be deleted'); END
    """)
    for name, table, columns in (
        (
            "detail_revision_posting",
            "posting_detail_revisions",
            "posting_id, revision_id DESC",
        ),
        ("detail_revision_run", "posting_detail_revisions", "run_id"),
        ("detail_first_run", "posting_details", "first_seen_run_id"),
        ("detail_last_run", "posting_details", "last_seen_run_id"),
        ("attempt_posting", "posting_fetch_attempts", "posting_id, started_at"),
        ("attempt_run_status", "posting_fetch_attempts", "run_id, status"),
        ("attempt_revision", "posting_fetch_attempts", "revision_id, posting_id"),
    ):
        connection.execute(f"CREATE INDEX idx_{name} ON {table}({columns})")
