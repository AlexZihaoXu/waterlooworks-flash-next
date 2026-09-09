"""Additive, non-authoritative source index and pay-candidate checkpoints."""

import sqlite3

_SCHEMA = (
    """
    CREATE TABLE source_extraction_runs (
        run_id TEXT PRIMARY KEY,
        configuration_hash TEXT NOT NULL CHECK(length(configuration_hash)=64),
        configuration_json TEXT NOT NULL CHECK(json_valid(configuration_json)),
        status TEXT NOT NULL
            CHECK(status IN ('running','stopped','completed','failed')),
        owner TEXT,
        lease_until_ms INTEGER NOT NULL DEFAULT 0,
        stop_requested INTEGER NOT NULL DEFAULT 0 CHECK(stop_requested IN (0,1)),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    ) STRICT
    """,
    """
    CREATE UNIQUE INDEX source_extraction_single_run
    ON source_extraction_runs(status) WHERE status='running'
    """,
    """
    CREATE TABLE source_extraction_items (
        run_id TEXT NOT NULL REFERENCES source_extraction_runs(run_id),
        posting_id TEXT NOT NULL REFERENCES postings(posting_id),
        content_revision_id INTEGER NOT NULL,
        content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
        mode TEXT NOT NULL CHECK(mode IN ('salary','keywords')),
        part TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK(status IN (
                'pending','running','accepted','partial','rejected','failed','stale'
            )),
        attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0 AND attempts<=2),
        error_category TEXT,
        rejected_count INTEGER NOT NULL DEFAULT 0 CHECK(rejected_count>=0),
        prompt_tokens INTEGER,
        completion_tokens INTEGER,
        reasoning_tokens INTEGER,
        seconds REAL,
        extraction_id INTEGER REFERENCES source_extractions(extraction_id),
        PRIMARY KEY(run_id,posting_id,mode,part),
        FOREIGN KEY(content_revision_id,posting_id)
            REFERENCES posting_content_revisions(revision_id,posting_id)
    ) STRICT
    """,
    """
    CREATE TABLE source_extractions (
        extraction_id INTEGER PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES source_extraction_runs(run_id),
        posting_id TEXT NOT NULL REFERENCES postings(posting_id),
        content_revision_id INTEGER NOT NULL,
        content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
        configuration_hash TEXT NOT NULL CHECK(length(configuration_hash)=64),
        mode TEXT NOT NULL CHECK(mode IN ('salary','keywords')),
        part TEXT NOT NULL,
        rejected_count INTEGER NOT NULL CHECK(rejected_count>=0),
        created_at TEXT NOT NULL,
        UNIQUE(configuration_hash,posting_id,content_hash,mode,part),
        FOREIGN KEY(content_revision_id,posting_id)
            REFERENCES posting_content_revisions(revision_id,posting_id)
    ) STRICT
    """,
    """
    CREATE TABLE source_extraction_terms (
        extraction_id INTEGER NOT NULL REFERENCES source_extractions(extraction_id),
        name TEXT NOT NULL CHECK(length(name)>0 AND length(name)<=120),
        field TEXT NOT NULL,
        start INTEGER NOT NULL CHECK(start>=0),
        end INTEGER NOT NULL CHECK(end>start),
        PRIMARY KEY(extraction_id,name,field,start,end)
    ) STRICT
    """,
    """
    CREATE TABLE source_extraction_pay (
        extraction_id INTEGER NOT NULL REFERENCES source_extractions(extraction_id),
        ordinal INTEGER NOT NULL CHECK(ordinal>=0),
        minimum TEXT,
        maximum TEXT,
        currency TEXT CHECK(currency IS NULL OR
            (length(currency)=3 AND currency NOT GLOB '*[^A-Z]*')),
        period TEXT CHECK(period IS NULL OR
            period IN ('hour','day','week','month','year')),
        bound_type TEXT NOT NULL
            CHECK(bound_type IN ('fixed','range','minimum','maximum')),
        minimum_inclusive INTEGER
            CHECK(minimum_inclusive IS NULL OR minimum_inclusive IN (0,1)),
        maximum_inclusive INTEGER
            CHECK(maximum_inclusive IS NULL OR maximum_inclusive IN (0,1)),
        field TEXT NOT NULL,
        quote_start INTEGER NOT NULL CHECK(quote_start>=0),
        quote_end INTEGER NOT NULL CHECK(quote_end>quote_start),
        amount_start INTEGER NOT NULL CHECK(amount_start>=quote_start),
        amount_end INTEGER NOT NULL
            CHECK(amount_end>amount_start AND amount_end<=quote_end),
        CHECK(minimum IS NOT NULL OR maximum IS NOT NULL),
        PRIMARY KEY(extraction_id,ordinal)
    ) STRICT
    """,
    """
    CREATE INDEX source_extraction_terms_name
    ON source_extraction_terms(name,extraction_id)
    """,
    """
    CREATE INDEX source_extraction_queue
    ON source_extraction_items(run_id,status,posting_id)
    """,
    """
    CREATE VIEW source_extractions_current AS
    SELECT e.* FROM source_extractions e JOIN posting_contents c
    ON c.posting_id=e.posting_id AND c.content_hash=e.content_hash
    """,
    """
    CREATE TRIGGER source_extractions_current_guard BEFORE INSERT ON source_extractions
    WHEN NOT EXISTS (
        SELECT 1 FROM posting_contents c JOIN posting_content_revisions r
        ON r.posting_id=c.posting_id AND r.content_hash=c.content_hash
        JOIN source_extraction_runs j ON j.run_id=NEW.run_id
        JOIN source_extraction_items i ON i.run_id=j.run_id
        AND i.posting_id=NEW.posting_id AND i.mode=NEW.mode AND i.part=NEW.part
        WHERE c.posting_id=NEW.posting_id AND c.content_hash=NEW.content_hash
        AND r.revision_id=NEW.content_revision_id
        AND i.content_revision_id=NEW.content_revision_id AND i.status='running'
        AND j.status='running' AND j.configuration_hash=NEW.configuration_hash
    ) BEGIN SELECT RAISE(ABORT,'Source extraction requires current saved content'); END
    """,
)


def migrate_source_extraction(connection: sqlite3.Connection) -> None:
    """Caller owns the transaction; existing projections are never replaced."""
    for statement in _SCHEMA:
        connection.execute(statement)
    for table, end_column in (
        ("source_extraction_terms", "end"),
        ("source_extraction_pay", "quote_end"),
    ):
        connection.execute(
            f"CREATE TRIGGER {table}_evidence_guard BEFORE INSERT ON {table} "
            "WHEN NOT EXISTS (SELECT 1 FROM source_extractions e "
            "JOIN posting_contents c ON c.posting_id=e.posting_id "
            "AND c.content_hash=e.content_hash "
            "JOIN json_each(c.content_json,'$.fields') f "
            "WHERE e.extraction_id=NEW.extraction_id AND f.key=NEW.field "
            f"AND f.type='text' AND NEW.{end_column}<=length(f.value)) "
            "BEGIN SELECT RAISE(ABORT,'Invalid source span'); END"
        )
    for table in (
        "source_extractions",
        "source_extraction_terms",
        "source_extraction_pay",
    ):
        for action in ("UPDATE", "DELETE"):
            connection.execute(
                f"CREATE TRIGGER {table}_no_{action.lower()} "
                f"BEFORE {action} ON {table} "
                "BEGIN SELECT RAISE(ABORT,'Immutable source extraction'); END"
            )
