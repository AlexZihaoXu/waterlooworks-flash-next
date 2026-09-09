"""SQLite v8 durable local inference jobs, cache and model projection pointers."""

import sqlite3

_MODEL_ID = "qwen3.8-27b@q4_k_m"
_ERROR_CATEGORIES = (
    "configuration",
    "model_unavailable",
    "timeout",
    "connection",
    "http_client",
    "http_server",
    "response_size",
    "response_schema",
    "thinking_boundary",
    "evidence_rejected",
    "stale_source",
    "stopped",
    "internal",
)


def _rebuild_compensation_currency(connection: sqlite3.Connection) -> None:
    """Permit any explicit uppercase ISO-style code while retaining exact rows."""
    for name in (
        "immutable_normalized_compensation_rates_update",
        "immutable_normalized_compensation_rates_delete",
        "source_identity_normalized_compensation_rates",
    ):
        connection.execute(f"DROP TRIGGER {name}")
    for name in ("idx_compensation_query", "idx_compensation_projection"):
        connection.execute(f"DROP INDEX {name}")
    connection.execute(
        "ALTER TABLE normalized_compensation_rates "
        "RENAME TO normalized_compensation_rates_v7"
    )
    connection.execute("""
        CREATE TABLE normalized_compensation_rates (
            rate_id INTEGER PRIMARY KEY,
            projection_id INTEGER NOT NULL
                REFERENCES analysis_projections(projection_id) ON DELETE RESTRICT,
            ordinal INTEGER NOT NULL CHECK(ordinal>=0),
            bound_type TEXT NOT NULL
                CHECK(bound_type IN ('fixed','range','minimum','maximum')),
            minimum_decimal TEXT CHECK(minimum_decimal IS NULL OR (
                length(minimum_decimal) BETWEEN 1 AND 64
                AND minimum_decimal NOT GLOB '*[^0-9.]*'
                AND minimum_decimal NOT GLOB '*.*.*'
                AND minimum_decimal GLOB '[0-9]*'
                AND (minimum_decimal='0' OR substr(minimum_decimal,1,1)<>'0'
                    OR substr(minimum_decimal,2,1)='.')
                AND (instr(minimum_decimal,'.')=0 OR
                    (instr(minimum_decimal,'.')<length(minimum_decimal)
                     AND substr(minimum_decimal,-1)<>'0')))),
            maximum_decimal TEXT CHECK(maximum_decimal IS NULL OR (
                length(maximum_decimal) BETWEEN 1 AND 64
                AND maximum_decimal NOT GLOB '*[^0-9.]*'
                AND maximum_decimal NOT GLOB '*.*.*'
                AND maximum_decimal GLOB '[0-9]*'
                AND (maximum_decimal='0' OR substr(maximum_decimal,1,1)<>'0'
                    OR substr(maximum_decimal,2,1)='.')
                AND (instr(maximum_decimal,'.')=0 OR
                    (instr(maximum_decimal,'.')<length(maximum_decimal)
                     AND substr(maximum_decimal,-1)<>'0')))),
            minimum_inclusive INTEGER CHECK(minimum_inclusive IN (0,1)),
            maximum_inclusive INTEGER CHECK(maximum_inclusive IN (0,1)),
            currency TEXT CHECK(currency IS NULL OR
                (length(currency)=3 AND currency NOT GLOB '*[^A-Z]*')),
            period TEXT CHECK(period IN ('hour','day','week','month','year')),
            hours_per_week_decimal TEXT CHECK(hours_per_week_decimal IS NULL OR (
                length(hours_per_week_decimal) BETWEEN 1 AND 64
                AND hours_per_week_decimal NOT GLOB '*[^0-9.]*'
                AND hours_per_week_decimal NOT GLOB '*.*.*'
                AND hours_per_week_decimal GLOB '[0-9]*'
                AND hours_per_week_decimal<>'0'
                AND (substr(hours_per_week_decimal,1,1)<>'0'
                    OR substr(hours_per_week_decimal,2,1)='.')
                AND (instr(hours_per_week_decimal,'.')=0 OR
                    (instr(hours_per_week_decimal,'.')<length(hours_per_week_decimal)
                     AND substr(hours_per_week_decimal,-1)<>'0')))),
            payout_frequency TEXT CHECK(payout_frequency IN
                ('weekly','biweekly','semi-monthly','monthly')),
            eligibility TEXT CHECK(eligibility IS NULL OR length(eligibility)<=1000),
            eligibility_field TEXT,
            eligibility_start INTEGER CHECK(eligibility_start>=0),
            eligibility_end INTEGER CHECK(eligibility_end>eligibility_start),
            source_field TEXT NOT NULL,
            source_start INTEGER NOT NULL CHECK(source_start>=0),
            source_end INTEGER NOT NULL CHECK(source_end>source_start),
            source_hash TEXT NOT NULL CHECK(length(source_hash)=64
                AND source_hash NOT GLOB '*[^0-9a-f]*'),
            UNIQUE(projection_id,ordinal),
            CHECK((minimum_decimal IS NULL)=(minimum_inclusive IS NULL)),
            CHECK((maximum_decimal IS NULL)=(maximum_inclusive IS NULL)),
            CHECK(minimum_decimal IS NOT NULL OR maximum_decimal IS NOT NULL),
            CHECK((eligibility IS NULL)=(eligibility_field IS NULL)),
            CHECK((eligibility IS NULL)=(eligibility_start IS NULL)),
            CHECK((eligibility IS NULL)=(eligibility_end IS NULL))
        ) STRICT
    """)
    columns = (
        "rate_id,projection_id,ordinal,bound_type,minimum_decimal,maximum_decimal,"
        "minimum_inclusive,maximum_inclusive,currency,period,"
        "hours_per_week_decimal,payout_frequency,eligibility,eligibility_field,"
        "eligibility_start,eligibility_end,source_field,source_start,source_end,"
        "source_hash"
    )
    connection.execute(
        f"INSERT INTO normalized_compensation_rates({columns}) "
        f"SELECT {columns} FROM normalized_compensation_rates_v7"
    )
    connection.execute("DROP TABLE normalized_compensation_rates_v7")
    connection.execute(
        "CREATE INDEX idx_compensation_query ON normalized_compensation_rates"
        "(currency,period,minimum_decimal,maximum_decimal)"
    )
    connection.execute(
        "CREATE INDEX idx_compensation_projection ON "
        "normalized_compensation_rates(projection_id,ordinal)"
    )
    for operation in ("UPDATE", "DELETE"):
        connection.execute(f"""
            CREATE TRIGGER immutable_normalized_compensation_rates_{operation.lower()}
            BEFORE {operation} ON normalized_compensation_rates
            BEGIN SELECT RAISE(ABORT,'Normalized evidence is immutable'); END
        """)
    connection.execute("""
        CREATE TRIGGER source_identity_normalized_compensation_rates
        BEFORE INSERT ON normalized_compensation_rates
        WHEN NOT EXISTS(SELECT 1 FROM analysis_projections
            WHERE projection_id=NEW.projection_id
              AND content_hash=NEW.source_hash)
        BEGIN SELECT RAISE(ABORT,'Span evidence source identity mismatch'); END
    """)


def migrate_inference(connection: sqlite3.Connection) -> None:
    """Create v8 orchestration schema; no job, provider call or auto-resume."""
    _rebuild_compensation_currency(connection)
    categories = ",".join(f"'{value}'" for value in _ERROR_CATEGORIES)
    connection.execute(f"""
        CREATE TABLE inference_jobs (
            job_id TEXT PRIMARY KEY NOT NULL,
            status TEXT NOT NULL CHECK(status IN
                ('pending','running','stopping','completed','failed','interrupted')),
            model_id TEXT NOT NULL CHECK(model_id='{_MODEL_ID}'),
            endpoint TEXT NOT NULL CHECK(endpoint='http://127.0.0.1:1234'),
            input_version TEXT NOT NULL,
            template_version TEXT NOT NULL,
            validator_version TEXT NOT NULL,
            max_tokens INTEGER NOT NULL CHECK(max_tokens BETWEEN 1 AND 4096),
            concurrency INTEGER NOT NULL CHECK(concurrency BETWEEN 1 AND 4),
            max_attempts INTEGER NOT NULL CHECK(max_attempts BETWEEN 1 AND 3),
            timeout_seconds INTEGER NOT NULL CHECK(timeout_seconds BETWEEN 1 AND 600),
            created_at TEXT NOT NULL CHECK(datetime(created_at) IS NOT NULL),
            started_at TEXT CHECK(started_at IS NULL
                OR datetime(started_at) IS NOT NULL),
            finished_at TEXT CHECK(finished_at IS NULL
                OR datetime(finished_at) IS NOT NULL),
            lease_expires_at TEXT CHECK(lease_expires_at IS NULL
                OR datetime(lease_expires_at) IS NOT NULL),
            stop_requested INTEGER NOT NULL DEFAULT 0 CHECK(stop_requested IN (0,1)),
            error_category TEXT CHECK(error_category IN ({categories})),
            CHECK(status IN ('pending','running','stopping')
                OR finished_at IS NOT NULL),
            CHECK(status<>'running' OR lease_expires_at IS NOT NULL)
        ) STRICT
    """)
    connection.execute("""
        CREATE TABLE inference_cache (
            cache_id INTEGER PRIMARY KEY,
            input_hash TEXT NOT NULL CHECK(length(input_hash)=64
                AND input_hash NOT GLOB '*[^0-9a-f]*'),
            content_hash TEXT NOT NULL CHECK(length(content_hash)=64
                AND content_hash NOT GLOB '*[^0-9a-f]*'),
            model_id TEXT NOT NULL,
            input_version TEXT NOT NULL,
            template_version TEXT NOT NULL,
            validator_version TEXT NOT NULL,
            max_tokens INTEGER NOT NULL,
            projection_id INTEGER NOT NULL UNIQUE
                REFERENCES analysis_projections(projection_id) ON DELETE RESTRICT,
            created_at TEXT NOT NULL CHECK(datetime(created_at) IS NOT NULL),
            UNIQUE(input_hash,content_hash,model_id,input_version,
                template_version,validator_version,max_tokens)
        ) STRICT
    """)
    connection.execute(f"""
        CREATE TABLE inference_job_items (
            job_id TEXT NOT NULL REFERENCES inference_jobs(job_id) ON DELETE RESTRICT,
            posting_id TEXT NOT NULL REFERENCES postings(posting_id) ON DELETE RESTRICT,
            ordinal INTEGER NOT NULL CHECK(ordinal>=0),
            selected_listing_revision_id INTEGER NOT NULL,
            selected_content_revision_id INTEGER NOT NULL,
            selected_listing_hash TEXT NOT NULL,
            selected_content_hash TEXT NOT NULL,
            input_hash TEXT NOT NULL CHECK(length(input_hash)=64
                AND input_hash NOT GLOB '*[^0-9a-f]*'),
            request_compensation INTEGER NOT NULL
                CHECK(request_compensation IN (0,1)),
            request_skills INTEGER NOT NULL CHECK(request_skills IN (0,1)),
            status TEXT NOT NULL CHECK(status IN
                ('pending','running','accepted','rejected','failed',
                 'not_needed','interrupted')),
            attempt_count INTEGER NOT NULL DEFAULT 0
                CHECK(attempt_count BETWEEN 0 AND 3),
            cache_hit INTEGER NOT NULL DEFAULT 0 CHECK(cache_hit IN (0,1)),
            cache_id INTEGER REFERENCES inference_cache(cache_id) ON DELETE RESTRICT,
            projection_id INTEGER REFERENCES analysis_projections(projection_id)
                ON DELETE RESTRICT,
            error_category TEXT CHECK(error_category IN ({categories})),
            started_at TEXT CHECK(started_at IS NULL
                OR datetime(started_at) IS NOT NULL),
            finished_at TEXT CHECK(finished_at IS NULL
                OR datetime(finished_at) IS NOT NULL),
            PRIMARY KEY(job_id,posting_id),
            UNIQUE(job_id,ordinal),
            FOREIGN KEY(selected_listing_revision_id,posting_id)
                REFERENCES posting_revisions(revision_id,posting_id) ON DELETE RESTRICT,
            FOREIGN KEY(selected_content_revision_id,posting_id)
                REFERENCES posting_content_revisions(revision_id,posting_id)
                ON DELETE RESTRICT,
            CHECK((status='accepted')=(projection_id IS NOT NULL)),
            CHECK(cache_hit=0 OR cache_id IS NOT NULL),
            CHECK(status NOT IN ('accepted','rejected','failed','not_needed')
                OR finished_at IS NOT NULL)
        ) WITHOUT ROWID
    """)
    connection.execute("""
        CREATE TABLE posting_model_projection_current (
            posting_id TEXT PRIMARY KEY NOT NULL
                REFERENCES postings(posting_id) ON DELETE RESTRICT,
            projection_id INTEGER NOT NULL UNIQUE
                REFERENCES analysis_projections(projection_id) ON DELETE RESTRICT,
            input_hash TEXT NOT NULL,
            cache_id INTEGER NOT NULL
                REFERENCES inference_cache(cache_id) ON DELETE RESTRICT,
            updated_at TEXT NOT NULL CHECK(datetime(updated_at) IS NOT NULL)
        ) STRICT
    """)
    connection.execute("""
        CREATE VIEW effective_model_projections AS
        SELECT pointer.posting_id,pointer.projection_id,pointer.input_hash,
               pointer.cache_id,pointer.updated_at
        FROM posting_model_projection_current pointer
        JOIN analysis_projections projection USING(projection_id)
        JOIN posting_contents content USING(posting_id)
        WHERE projection.content_hash=content.content_hash
          AND projection.producer_kind='model'
    """)
    for name, table, columns in (
        ("inference_jobs_status", "inference_jobs", "status,created_at"),
        ("inference_items_status", "inference_job_items", "job_id,status,ordinal"),
        (
            "inference_items_source",
            "inference_job_items",
            "posting_id,selected_content_hash",
        ),
        (
            "inference_cache_lookup",
            "inference_cache",
            "content_hash,input_hash,model_id",
        ),
    ):
        connection.execute(f"CREATE INDEX idx_{name} ON {table}({columns})")
    connection.execute("""
        CREATE TRIGGER inference_cache_projection BEFORE INSERT ON inference_cache
        WHEN NOT EXISTS(SELECT 1 FROM analysis_projections
            WHERE projection_id=NEW.projection_id AND producer_kind='model'
              AND content_hash=NEW.content_hash)
        BEGIN SELECT RAISE(ABORT,'Inference cache projection mismatch'); END
    """)
    connection.execute("""
        CREATE TRIGGER model_pointer_projection
        BEFORE INSERT ON posting_model_projection_current
        WHEN NOT EXISTS(SELECT 1 FROM analysis_projections
            WHERE projection_id=NEW.projection_id AND posting_id=NEW.posting_id
              AND producer_kind='model')
          OR NOT EXISTS(SELECT 1 FROM inference_cache cache
            JOIN analysis_projections projection
              ON projection.projection_id=NEW.projection_id
            WHERE cache.cache_id=NEW.cache_id
              AND cache.input_hash=NEW.input_hash
              AND cache.content_hash=projection.content_hash)
        BEGIN SELECT RAISE(ABORT,'Model projection pointer mismatch'); END
    """)
    connection.execute("""
        CREATE TRIGGER model_pointer_projection_update
        BEFORE UPDATE ON posting_model_projection_current
        WHEN NOT EXISTS(SELECT 1 FROM analysis_projections
            WHERE projection_id=NEW.projection_id AND posting_id=NEW.posting_id
              AND producer_kind='model')
          OR NOT EXISTS(SELECT 1 FROM inference_cache cache
            JOIN analysis_projections projection
              ON projection.projection_id=NEW.projection_id
            WHERE cache.cache_id=NEW.cache_id
              AND cache.input_hash=NEW.input_hash
              AND cache.content_hash=projection.content_hash)
        BEGIN SELECT RAISE(ABORT,'Model projection pointer mismatch'); END
    """)
    for table in ("inference_cache",):
        for operation in ("UPDATE", "DELETE"):
            connection.execute(f"""
                CREATE TRIGGER immutable_{table}_{operation.lower()}
                BEFORE {operation} ON {table}
                BEGIN SELECT RAISE(ABORT,'Inference cache is immutable'); END
            """)
    connection.execute("""
        CREATE TRIGGER model_pointer_no_delete
        BEFORE DELETE ON posting_model_projection_current
        BEGIN SELECT RAISE(ABORT,'Model projection pointer cannot be deleted'); END
    """)
