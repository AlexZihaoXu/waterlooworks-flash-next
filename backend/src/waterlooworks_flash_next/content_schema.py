"""SQLite v4: plain-text content is independent of v3 metadata-only snapshots."""

import sqlite3

from waterlooworks_flash_next.content import CONTENT_LABELS
from waterlooworks_flash_next.detail_schema import DETAIL_ERROR_CATEGORIES


def migrate_content(connection: sqlite3.Connection) -> None:
    """Caller owns the migration transaction; v3 tables/triggers remain untouched."""
    connection.execute("""
        CREATE TABLE posting_contents (
            posting_id TEXT PRIMARY KEY NOT NULL REFERENCES postings(posting_id),
            content_json TEXT NOT NULL CHECK(json_valid(content_json))
                CHECK(coalesce(json_type(content_json)='object' AND
                    json_type(content_json,'$.id')='text' AND
                    json_extract(content_json,'$.id')=posting_id AND
                    json_extract(content_json,'$.coverage')='content_only' AND
                    json_type(content_json,'$.fields')='object',0))
                CHECK(json_remove(content_json,'$.id','$.coverage','$.fields')='{}'),
            content_hash TEXT NOT NULL CHECK(length(content_hash)=64
                AND content_hash NOT GLOB '*[^0-9a-f]*'),
            extractor_version TEXT NOT NULL,
            first_seen_at TEXT NOT NULL CHECK(datetime(first_seen_at) IS NOT NULL),
            last_seen_at TEXT NOT NULL CHECK(datetime(last_seen_at) IS NOT NULL),
            first_seen_run_id TEXT NOT NULL REFERENCES collection_runs(id),
            last_seen_run_id TEXT NOT NULL REFERENCES collection_runs(id),
            CHECK(julianday(last_seen_at)>=julianday(first_seen_at))
        ) STRICT
    """)
    labels = ",".join(
        "'" + label.replace("'", "''") + "'" for label in sorted(CONTENT_LABELS)
    )
    for operation in ("INSERT", "UPDATE"):
        connection.execute(f"""
            CREATE TRIGGER validate_content_fields_{operation.lower()}
            BEFORE {operation} ON posting_contents
            WHEN EXISTS(SELECT 1 FROM json_each(NEW.content_json,'$.fields')
                WHERE type<>'text' OR key NOT IN ({labels})
                    OR length(trim(value))=0 OR length(value)>200000)
                OR json_type(NEW.content_json,'$.fields.\"Job Title:\"') IS NOT 'text'
                OR json_type(NEW.content_json,'$.fields.\"Organization:\"')
                    IS NOT 'text'
                OR json_type(NEW.content_json,'$.fields.\"Work Term:\"') IS NOT 'text'
            BEGIN SELECT RAISE(ABORT,'Content fields violate the contract'); END
        """)
    connection.execute("""
        CREATE TABLE posting_content_revisions (
            revision_id INTEGER PRIMARY KEY,
            posting_id TEXT NOT NULL REFERENCES posting_contents(posting_id),
            content_json TEXT NOT NULL CHECK(json_valid(content_json)),
            content_hash TEXT NOT NULL CHECK(length(content_hash)=64
                AND content_hash NOT GLOB '*[^0-9a-f]*'),
            extractor_version TEXT NOT NULL,
            run_id TEXT NOT NULL REFERENCES collection_runs(id),
            observed_at TEXT NOT NULL CHECK(datetime(observed_at) IS NOT NULL),
            UNIQUE(revision_id,posting_id)
        ) STRICT
    """)
    categories = ",".join(f"'{value}'" for value in DETAIL_ERROR_CATEGORIES)
    connection.execute(f"""
        CREATE TABLE posting_content_attempts (
            attempt_id TEXT PRIMARY KEY NOT NULL,
            posting_id TEXT NOT NULL REFERENCES postings(posting_id),
            run_id TEXT NOT NULL REFERENCES collection_runs(id),
            started_at TEXT NOT NULL CHECK(datetime(started_at) IS NOT NULL),
            finished_at TEXT CHECK(finished_at IS NULL
                OR datetime(finished_at) IS NOT NULL),
            status TEXT NOT NULL DEFAULT 'running'
                CHECK(status IN ('running','succeeded','failed','interrupted')),
            request_count INTEGER NOT NULL DEFAULT 0 CHECK(request_count>=0),
            retry_count INTEGER NOT NULL DEFAULT 0
                CHECK(retry_count>=0 AND retry_count<=request_count),
            http_status INTEGER CHECK(http_status BETWEEN 100 AND 599),
            error_category TEXT CHECK(error_category IN ({categories})),
            revision_id INTEGER,
            FOREIGN KEY(revision_id,posting_id)
                REFERENCES posting_content_revisions(revision_id,posting_id),
            CHECK((status='running' AND finished_at IS NULL
                    AND error_category IS NULL AND revision_id IS NULL)
                OR (status='succeeded' AND finished_at IS NOT NULL
                    AND http_status=200 AND error_category IS NULL
                    AND revision_id IS NOT NULL)
                OR (status IN ('failed','interrupted') AND finished_at IS NOT NULL
                    AND error_category IS NOT NULL AND revision_id IS NULL)),
            CHECK(finished_at IS NULL OR julianday(finished_at)>=julianday(started_at))
        ) STRICT
    """)
    connection.execute("""
        CREATE TRIGGER content_revision_matches_current
        BEFORE INSERT ON posting_content_revisions
        WHEN NOT EXISTS(SELECT 1 FROM posting_contents WHERE posting_id=NEW.posting_id
            AND content_json=NEW.content_json AND content_hash=NEW.content_hash
            AND extractor_version=NEW.extractor_version AND last_seen_run_id=NEW.run_id
            AND last_seen_at=NEW.observed_at)
        BEGIN SELECT RAISE(ABORT,'Content revision must match current'); END
    """)
    for operation in ("UPDATE", "DELETE"):
        connection.execute(f"""
            CREATE TRIGGER immutable_content_revision_{operation.lower()}
            BEFORE {operation} ON posting_content_revisions
            BEGIN SELECT RAISE(ABORT,'Content history is append-only'); END
        """)
    connection.execute("""
        CREATE TRIGGER terminal_content_attempt_immutable
        BEFORE UPDATE ON posting_content_attempts
        WHEN OLD.status<>'running' OR NEW.attempt_id<>OLD.attempt_id
            OR NEW.posting_id<>OLD.posting_id OR NEW.run_id<>OLD.run_id
            OR NEW.started_at<>OLD.started_at OR NEW.request_count<OLD.request_count
            OR NEW.retry_count<OLD.retry_count
        BEGIN SELECT RAISE(ABORT,'Content attempt mutation forbidden'); END
    """)
    connection.execute("""
        CREATE TRIGGER content_attempt_no_delete
        BEFORE DELETE ON posting_content_attempts
        BEGIN SELECT RAISE(ABORT,'Content attempts cannot be deleted'); END
    """)
    for name, table, columns in (
        (
            "content_revision_posting",
            "posting_content_revisions",
            "posting_id,revision_id DESC",
        ),
        ("content_revision_run", "posting_content_revisions", "run_id"),
        ("content_attempt_run", "posting_content_attempts", "run_id,status"),
        (
            "content_attempt_posting",
            "posting_content_attempts",
            "posting_id,started_at",
        ),
    ):
        connection.execute(f"CREATE INDEX idx_{name} ON {table}({columns})")
    for name, declaration in (
        ("details_completed", "INTEGER NOT NULL DEFAULT 0 CHECK(details_completed>=0)"),
        ("details_total", "INTEGER NOT NULL DEFAULT 0 CHECK(details_total>=0)"),
        ("detail_requests", "INTEGER NOT NULL DEFAULT 0 CHECK(detail_requests>=0)"),
        ("detail_retries", "INTEGER NOT NULL DEFAULT 0 CHECK(detail_retries>=0)"),
        ("detail_limited", "INTEGER NOT NULL DEFAULT 0 CHECK(detail_limited IN (0,1))"),
        (
            "blocked_until",
            "TEXT CHECK(blocked_until IS NULL OR datetime(blocked_until) IS NOT NULL)",
        ),
    ):
        connection.execute(
            f"ALTER TABLE collection_runs ADD COLUMN {name} {declaration}"
        )
