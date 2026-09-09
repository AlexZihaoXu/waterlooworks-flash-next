"""SQLite v7 normalized, source-versioned deterministic projections."""

import sqlite3


def migrate_normalized_storage(connection: sqlite3.Connection) -> None:
    """Create normalized projection storage; caller owns the migration transaction."""
    connection.execute(
        "CREATE UNIQUE INDEX idx_revision_identity "
        "ON posting_revisions(revision_id,posting_id)"
    )
    connection.execute("""
        CREATE TABLE analysis_projections (
            projection_id INTEGER PRIMARY KEY,
            posting_id TEXT NOT NULL REFERENCES postings(posting_id) ON DELETE RESTRICT,
            listing_revision_id INTEGER NOT NULL,
            content_revision_id INTEGER NOT NULL,
            listing_hash TEXT NOT NULL CHECK(length(listing_hash)=64
                AND listing_hash NOT GLOB '*[^0-9a-f]*'),
            content_hash TEXT NOT NULL CHECK(length(content_hash)=64
                AND content_hash NOT GLOB '*[^0-9a-f]*'),
            producer_kind TEXT NOT NULL
                CHECK(producer_kind IN ('deterministic_parser','model')),
            producer_name TEXT NOT NULL CHECK(length(producer_name) BETWEEN 1 AND 100),
            producer_version TEXT NOT NULL
                CHECK(length(producer_version) BETWEEN 1 AND 100),
            parser_version TEXT NOT NULL
                CHECK(length(parser_version) BETWEEN 1 AND 100),
            created_at TEXT NOT NULL CHECK(datetime(created_at) IS NOT NULL),
            UNIQUE(posting_id,listing_revision_id,content_revision_id,
                producer_kind,producer_name,producer_version,parser_version),
            FOREIGN KEY(listing_revision_id,posting_id)
                REFERENCES posting_revisions(revision_id,posting_id) ON DELETE RESTRICT,
            FOREIGN KEY(content_revision_id,posting_id)
                REFERENCES posting_content_revisions(revision_id,posting_id)
                ON DELETE RESTRICT
        ) STRICT
    """)
    connection.execute("""
        CREATE TABLE posting_projection_current (
            posting_id TEXT PRIMARY KEY NOT NULL
                REFERENCES postings(posting_id) ON DELETE RESTRICT,
            deterministic_projection_id INTEGER NOT NULL UNIQUE
                REFERENCES analysis_projections(projection_id) ON DELETE RESTRICT,
            updated_at TEXT NOT NULL CHECK(datetime(updated_at) IS NOT NULL)
        ) STRICT
    """)
    connection.execute("""
        CREATE TABLE normalized_fixed_fields (
            projection_id INTEGER PRIMARY KEY
                REFERENCES analysis_projections(projection_id) ON DELETE RESTRICT,
            arrangement_state TEXT NOT NULL
                CHECK(arrangement_state IN ('recognized','unknown','needs_analysis')),
            arrangement TEXT CHECK(arrangement IN ('Remote','Hybrid','In-person')),
            city_state TEXT NOT NULL
                CHECK(city_state IN ('recognized','unknown','needs_analysis')),
            city TEXT,
            province_state TEXT NOT NULL
                CHECK(province_state IN ('recognized','unknown','needs_analysis')),
            province TEXT,
            country_state TEXT NOT NULL
                CHECK(country_state IN ('recognized','unknown','needs_analysis')),
            country TEXT,
            levels_state TEXT NOT NULL
                CHECK(levels_state IN ('recognized','unknown','needs_analysis')),
            work_term_state TEXT NOT NULL
                CHECK(work_term_state IN ('recognized','unknown','needs_analysis')),
            work_term_year INTEGER CHECK(work_term_year>0),
            work_term_season TEXT
                CHECK(work_term_season IN ('Winter','Spring','Summer','Fall')),
            duration_state TEXT NOT NULL
                CHECK(duration_state IN ('recognized','unknown','needs_analysis')),
            duration_count INTEGER CHECK(duration_count>0),
            duration_unit TEXT CHECK(duration_unit IN ('month','work_term')),
            duration_requirement TEXT CHECK(duration_requirement IN
                ('required','preferred','unspecified')),
            job_type_state TEXT NOT NULL
                CHECK(job_type_state IN ('recognized','unknown','needs_analysis')),
            job_type TEXT,
            targeted_disciplines_state TEXT NOT NULL CHECK(
                targeted_disciplines_state IN
                    ('recognized','unknown','needs_analysis')),
            application_count_state TEXT NOT NULL CHECK(
                application_count_state IN ('recognized','unknown','needs_analysis')),
            application_count INTEGER CHECK(application_count>=0),
            openings_state TEXT NOT NULL
                CHECK(openings_state IN ('recognized','unknown','needs_analysis')),
            openings INTEGER CHECK(openings>=0),
            deadline_state TEXT NOT NULL
                CHECK(deadline_state IN ('recognized','unknown','needs_analysis')),
            deadline_utc TEXT CHECK(deadline_utc IS NULL
                OR datetime(deadline_utc) IS NOT NULL),
            CHECK((arrangement_state='recognized')=(arrangement IS NOT NULL)),
            CHECK((city_state='recognized')=(city IS NOT NULL)),
            CHECK((province_state='recognized')=(province IS NOT NULL)),
            CHECK((country_state='recognized')=(country IS NOT NULL)),
            CHECK((work_term_state='recognized')=
                (work_term_year IS NOT NULL AND work_term_season IS NOT NULL)),
            CHECK((duration_state='recognized')=(duration_count IS NOT NULL
                AND duration_unit IS NOT NULL AND duration_requirement IS NOT NULL)),
            CHECK((job_type_state='recognized')=(job_type IS NOT NULL)),
            CHECK((application_count_state='recognized')=
                (application_count IS NOT NULL)),
            CHECK((openings_state='recognized')=(openings IS NOT NULL)),
            CHECK((deadline_state='recognized')=(deadline_utc IS NOT NULL))
        ) STRICT
    """)
    connection.execute("""
        CREATE TABLE normalized_projection_results (
            projection_id INTEGER PRIMARY KEY
                REFERENCES analysis_projections(projection_id) ON DELETE RESTRICT,
            skills_state TEXT NOT NULL
                CHECK(skills_state IN ('recognized','unknown','needs_analysis')),
            skills_coverage TEXT NOT NULL CHECK(
                skills_coverage='bounded_explicit_vocabulary'),
            compensation_state TEXT NOT NULL
                CHECK(compensation_state IN ('recognized','unknown','needs_analysis')),
            compensation_reason TEXT NOT NULL CHECK(compensation_reason IN
                ('clear_pay_expression','missing_or_no_amount',
                 'complex_or_unsupported','invalid_amount_or_bounds')),
            application_state TEXT NOT NULL
                CHECK(application_state IN ('recognized','unknown','needs_analysis')),
            external_application_required INTEGER
                CHECK(external_application_required IN (0,1)),
            application_route TEXT NOT NULL CHECK(application_route IN
                ('waterlooworks_and_employer','employer','unknown')),
            CHECK((application_state='recognized')=
                (external_application_required IS NOT NULL))
        ) STRICT
    """)
    connection.execute("""
        CREATE TABLE normalized_field_evidence (
            projection_id INTEGER NOT NULL
                REFERENCES analysis_projections(projection_id) ON DELETE RESTRICT,
            field_key TEXT NOT NULL CHECK(field_key IN
                ('arrangement','city','province','country','levels','work_term',
                 'duration','job_type','targeted_disciplines','application_count',
                 'openings','deadline','application_method','compensation')),
            source_kind TEXT NOT NULL CHECK(source_kind IN ('listing','content')),
            source_field TEXT NOT NULL,
            source_present INTEGER NOT NULL CHECK(source_present IN (0,1)),
            source_hash TEXT CHECK(source_hash IS NULL OR
                (length(source_hash)=64 AND source_hash NOT GLOB '*[^0-9a-f]*')),
            PRIMARY KEY(projection_id,field_key)
        ) WITHOUT ROWID
    """)
    connection.execute("""
        CREATE TABLE normalized_levels (
            projection_id INTEGER NOT NULL
                REFERENCES analysis_projections(projection_id) ON DELETE RESTRICT,
            ordinal INTEGER NOT NULL CHECK(ordinal>=0),
            level TEXT NOT NULL CHECK(level IN ('Junior','Intermediate','Senior')),
            PRIMARY KEY(projection_id,ordinal),
            UNIQUE(projection_id,level)
        ) WITHOUT ROWID
    """)
    connection.execute("""
        CREATE TABLE normalized_targeted_disciplines (
            projection_id INTEGER NOT NULL
                REFERENCES analysis_projections(projection_id) ON DELETE RESTRICT,
            ordinal INTEGER NOT NULL CHECK(ordinal>=0),
            discipline TEXT NOT NULL CHECK(length(discipline)>0),
            PRIMARY KEY(projection_id,ordinal),
            UNIQUE(projection_id,discipline)
        ) WITHOUT ROWID
    """)
    connection.execute("""
        CREATE TABLE normalized_field_unknown_values (
            projection_id INTEGER NOT NULL
                REFERENCES analysis_projections(projection_id) ON DELETE RESTRICT,
            field_key TEXT NOT NULL,
            ordinal INTEGER NOT NULL CHECK(ordinal>=0),
            value TEXT NOT NULL CHECK(length(value)>0),
            PRIMARY KEY(projection_id,field_key,ordinal)
        ) WITHOUT ROWID
    """)
    connection.execute("""
        CREATE TABLE normalized_skills (
            skill_id INTEGER PRIMARY KEY,
            canonical_name TEXT NOT NULL UNIQUE CHECK(length(canonical_name)>0)
        ) STRICT
    """)
    connection.execute("""
        CREATE TABLE normalized_skill_mentions (
            mention_id INTEGER PRIMARY KEY,
            projection_id INTEGER NOT NULL
                REFERENCES analysis_projections(projection_id) ON DELETE RESTRICT,
            skill_id INTEGER NOT NULL
                REFERENCES normalized_skills(skill_id) ON DELETE RESTRICT,
            ordinal INTEGER NOT NULL CHECK(ordinal>=0),
            requirement TEXT NOT NULL
                CHECK(requirement IN ('required','preferred','unspecified')),
            excluded_reason TEXT
                CHECK(excluded_reason IS NULL
                    OR excluded_reason='negated_or_optional_context'),
            source_field TEXT NOT NULL,
            source_start INTEGER NOT NULL CHECK(source_start>=0),
            source_end INTEGER NOT NULL CHECK(source_end>source_start),
            requirement_start INTEGER CHECK(requirement_start>=0),
            requirement_end INTEGER CHECK(requirement_end>requirement_start),
            source_hash TEXT NOT NULL CHECK(length(source_hash)=64
                AND source_hash NOT GLOB '*[^0-9a-f]*'),
            UNIQUE(projection_id,ordinal),
            CHECK((requirement_start IS NULL)=(requirement_end IS NULL)),
            CHECK(excluded_reason IS NULL OR requirement='unspecified')
        ) STRICT
    """)
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
            currency TEXT CHECK(currency IN
                ('CAD','USD','EUR','GBP','AUD','CHF','JPY')),
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
            eligibility TEXT,
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
    connection.execute("""
        CREATE TABLE normalized_application_instructions (
            projection_id INTEGER NOT NULL
                REFERENCES analysis_projections(projection_id) ON DELETE RESTRICT,
            ordinal INTEGER NOT NULL CHECK(ordinal>=0),
            source_field TEXT NOT NULL,
            source_start INTEGER NOT NULL CHECK(source_start>=0),
            source_end INTEGER NOT NULL CHECK(source_end>source_start),
            source_hash TEXT NOT NULL CHECK(length(source_hash)=64
                AND source_hash NOT GLOB '*[^0-9a-f]*'),
            PRIMARY KEY(projection_id,ordinal)
        ) WITHOUT ROWID
    """)
    connection.execute("""
        CREATE TABLE normalized_application_links (
            link_id INTEGER PRIMARY KEY,
            projection_id INTEGER NOT NULL,
            ordinal INTEGER NOT NULL CHECK(ordinal>=0),
            url TEXT NOT NULL CHECK(length(url)>0),
            classification TEXT NOT NULL CHECK(classification IN
                ('required','optional','not_required','uncertain','informational')),
            source_field TEXT NOT NULL,
            source_start INTEGER NOT NULL CHECK(source_start>=0),
            source_end INTEGER NOT NULL CHECK(source_end>source_start),
            instruction_ordinal INTEGER NOT NULL,
            source_hash TEXT NOT NULL CHECK(length(source_hash)=64
                AND source_hash NOT GLOB '*[^0-9a-f]*'),
            UNIQUE(projection_id,ordinal),
            FOREIGN KEY(projection_id,instruction_ordinal)
                REFERENCES normalized_application_instructions(projection_id,ordinal)
                ON DELETE RESTRICT
        ) STRICT
    """)
    for name, table, columns in (
        (
            "projection_source",
            "analysis_projections",
            "posting_id,listing_revision_id,content_revision_id",
        ),
        (
            "projection_producer",
            "analysis_projections",
            "producer_kind,producer_name,producer_version",
        ),
        ("fixed_arrangement", "normalized_fixed_fields", "arrangement,projection_id"),
        (
            "fixed_geography",
            "normalized_fixed_fields",
            "country,province,city,projection_id",
        ),
        (
            "fixed_counts",
            "normalized_fixed_fields",
            "application_count,openings,projection_id",
        ),
        ("fixed_deadline", "normalized_fixed_fields", "deadline_utc,projection_id"),
        ("level_value", "normalized_levels", "level,projection_id"),
        (
            "discipline_value",
            "normalized_targeted_disciplines",
            "discipline,projection_id",
        ),
        (
            "skill_mentions_skill",
            "normalized_skill_mentions",
            "skill_id,requirement,projection_id",
        ),
        (
            "skill_mentions_projection",
            "normalized_skill_mentions",
            "projection_id,source_field",
        ),
        (
            "compensation_query",
            "normalized_compensation_rates",
            "currency,period,minimum_decimal,maximum_decimal",
        ),
        (
            "compensation_projection",
            "normalized_compensation_rates",
            "projection_id,ordinal",
        ),
        (
            "application_required",
            "normalized_projection_results",
            "external_application_required,application_route",
        ),
        (
            "application_link_class",
            "normalized_application_links",
            "classification,projection_id",
        ),
        ("application_link_url", "normalized_application_links", "url"),
    ):
        connection.execute(f"CREATE INDEX idx_{name} ON {table}({columns})")
    connection.execute("""
        CREATE TRIGGER projection_source_identity BEFORE INSERT ON analysis_projections
        WHEN NOT EXISTS(SELECT 1 FROM posting_revisions
                WHERE revision_id=NEW.listing_revision_id
                  AND posting_id=NEW.posting_id AND content_hash=NEW.listing_hash)
          OR NOT EXISTS(SELECT 1 FROM posting_content_revisions
                WHERE revision_id=NEW.content_revision_id
                  AND posting_id=NEW.posting_id AND content_hash=NEW.content_hash)
        BEGIN SELECT RAISE(ABORT,'Projection source identity mismatch'); END
    """)
    for operation in ("INSERT", "UPDATE"):
        connection.execute(f"""
            CREATE TRIGGER current_projection_{operation.lower()}
            BEFORE {operation} ON posting_projection_current
            WHEN NOT EXISTS(
                SELECT 1 FROM analysis_projections a
                JOIN postings p ON p.posting_id=a.posting_id
                JOIN posting_contents c ON c.posting_id=a.posting_id
                WHERE a.projection_id=NEW.deterministic_projection_id
                  AND a.posting_id=NEW.posting_id
                  AND a.producer_kind='deterministic_parser'
                  AND a.listing_hash=p.content_hash
                  AND a.content_hash=c.content_hash
                  AND a.listing_revision_id=(SELECT max(revision_id)
                      FROM posting_revisions WHERE posting_id=a.posting_id)
                  AND a.content_revision_id=(SELECT max(revision_id)
                      FROM posting_content_revisions WHERE posting_id=a.posting_id))
            BEGIN SELECT RAISE(ABORT,'Current projection source is stale'); END
        """)
    for table in (
        "analysis_projections",
        "normalized_fixed_fields",
        "normalized_projection_results",
        "normalized_field_evidence",
        "normalized_levels",
        "normalized_targeted_disciplines",
        "normalized_field_unknown_values",
        "normalized_skills",
        "normalized_skill_mentions",
        "normalized_compensation_rates",
        "normalized_application_instructions",
        "normalized_application_links",
    ):
        for operation in ("UPDATE", "DELETE"):
            connection.execute(f"""
                CREATE TRIGGER immutable_{table}_{operation.lower()}
                BEFORE {operation} ON {table}
                BEGIN SELECT RAISE(ABORT,'Normalized evidence is immutable'); END
            """)
    for table in (
        "normalized_fixed_fields",
        "normalized_projection_results",
        "normalized_field_evidence",
        "normalized_levels",
        "normalized_targeted_disciplines",
        "normalized_field_unknown_values",
        "normalized_application_instructions",
        "normalized_application_links",
    ):
        connection.execute(f"""
            CREATE TRIGGER deterministic_only_{table}
            BEFORE INSERT ON {table}
            WHEN NOT EXISTS(SELECT 1 FROM analysis_projections
                WHERE projection_id=NEW.projection_id
                  AND producer_kind='deterministic_parser')
            BEGIN
                SELECT RAISE(ABORT,'Fixed output requires deterministic provenance');
            END
        """)
    connection.execute("""
        CREATE TRIGGER field_evidence_source_identity
        BEFORE INSERT ON normalized_field_evidence
        WHEN NOT EXISTS(SELECT 1 FROM analysis_projections
            WHERE projection_id=NEW.projection_id
              AND NEW.source_hash=CASE NEW.source_kind
                    WHEN 'listing' THEN listing_hash ELSE content_hash END)
        BEGIN SELECT RAISE(ABORT,'Field evidence source identity mismatch'); END
    """)
    for table in (
        "normalized_skill_mentions",
        "normalized_compensation_rates",
        "normalized_application_instructions",
        "normalized_application_links",
    ):
        connection.execute(f"""
            CREATE TRIGGER source_identity_{table}
            BEFORE INSERT ON {table}
            WHEN NOT EXISTS(SELECT 1 FROM analysis_projections
                WHERE projection_id=NEW.projection_id
                  AND content_hash=NEW.source_hash)
            BEGIN SELECT RAISE(ABORT,'Span evidence source identity mismatch'); END
        """)
    connection.execute("""
        CREATE TRIGGER current_projection_no_delete
        BEFORE DELETE ON posting_projection_current
        BEGIN SELECT RAISE(ABORT,'Current projection pointer cannot be deleted'); END
    """)
