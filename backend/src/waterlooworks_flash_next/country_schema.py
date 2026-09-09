"""Additive country predictions, separate from source facts and pay/keywords."""

import json
import sqlite3
from pathlib import Path

COUNTRIES: dict[str, str] = json.loads(
    Path(__file__).with_name("countries.json").read_text(encoding="utf-8")
)
COUNTRY_VERSION = "job-country-v1"


def migrate_country_inference(connection: sqlite3.Connection) -> None:
    """Caller owns transaction. ISO reference names are from MIT flag-icons."""
    statements = (
        "CREATE TABLE country_codes (code TEXT PRIMARY KEY, name TEXT NOT NULL) STRICT",
        """CREATE TABLE country_inference_runs (
            run_id TEXT PRIMARY KEY,
            configuration_hash TEXT NOT NULL CHECK(length(configuration_hash)=64),
            configuration_json TEXT NOT NULL CHECK(json_valid(configuration_json)),
            status TEXT NOT NULL
                CHECK(status IN ('running','stopped','completed','failed')),
            owner TEXT, lease_until_ms INTEGER NOT NULL DEFAULT 0,
            stop_requested INTEGER NOT NULL DEFAULT 0 CHECK(stop_requested IN (0,1)),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        ) STRICT""",
        """CREATE UNIQUE INDEX country_inference_single_run
        ON country_inference_runs(status) WHERE status='running'""",
        """CREATE TABLE country_inference_items (
            run_id TEXT NOT NULL REFERENCES country_inference_runs(run_id),
            posting_id TEXT NOT NULL REFERENCES postings(posting_id),
            listing_revision_id INTEGER NOT NULL,
            content_revision_id INTEGER,
            listing_hash TEXT NOT NULL CHECK(length(listing_hash)=64),
            content_hash TEXT CHECK(content_hash IS NULL OR length(content_hash)=64),
            input_hash TEXT NOT NULL CHECK(length(input_hash)=64),
            status TEXT NOT NULL CHECK(status IN
                ('pending','running','accepted','unknown','failed','stale')),
            attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 1),
            country TEXT REFERENCES country_codes(code),
            confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0.85 AND 1),
            reason TEXT CHECK(reason IS NULL OR reason IN (
                'location_context','ambiguous_location','no_location_context',
                'multiple_countries','low_confidence','input_too_large',
                'invalid_output','invalid_evidence','infrastructure','interrupted')),
            evidence_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(evidence_json)
                AND json_type(evidence_json)='array'
                AND json_array_length(evidence_json)<=3),
            origin_run_id TEXT REFERENCES country_inference_runs(run_id),
            PRIMARY KEY(run_id,posting_id),
            FOREIGN KEY(listing_revision_id,posting_id)
                REFERENCES posting_revisions(revision_id,posting_id),
            FOREIGN KEY(content_revision_id,posting_id)
                REFERENCES posting_content_revisions(revision_id,posting_id),
            CHECK((content_hash IS NULL)=(content_revision_id IS NULL)),
            CHECK((status='accepted')=(country IS NOT NULL)),
            CHECK((status='accepted')=(confidence IS NOT NULL)),
            CHECK(status!='accepted' OR (reason='location_context'
                AND json_array_length(evidence_json)>0)),
            CHECK(status='accepted' OR json_array_length(evidence_json)=0)
        ) STRICT""",
        """CREATE INDEX country_inference_queue
        ON country_inference_items(run_id,status,posting_id)""",
        """CREATE INDEX country_inference_posting_runs
        ON country_inference_items(posting_id,run_id)""",
        """CREATE TRIGGER country_inference_source_guard
        BEFORE INSERT ON country_inference_items
        WHEN NOT EXISTS (
            SELECT 1 FROM postings p
            JOIN posting_revisions lr ON lr.posting_id=p.posting_id
            AND lr.revision_id=NEW.listing_revision_id
            AND lr.content_hash=NEW.listing_hash
            LEFT JOIN posting_contents c ON c.posting_id=p.posting_id
            LEFT JOIN posting_content_revisions cr ON cr.posting_id=p.posting_id
            AND cr.revision_id=NEW.content_revision_id
            WHERE p.posting_id=NEW.posting_id AND p.content_hash=NEW.listing_hash
            AND c.content_hash IS NEW.content_hash
            AND cr.content_hash IS NEW.content_hash
        ) BEGIN SELECT RAISE(ABORT,'Country requires current sources'); END""",
        """CREATE TRIGGER country_inference_item_identity_no_update
        BEFORE UPDATE ON country_inference_items
        WHEN NEW.run_id!=OLD.run_id OR NEW.posting_id!=OLD.posting_id
            OR NEW.listing_revision_id!=OLD.listing_revision_id
            OR NEW.content_revision_id IS NOT OLD.content_revision_id
            OR NEW.listing_hash!=OLD.listing_hash
            OR NEW.content_hash IS NOT OLD.content_hash
            OR NEW.input_hash!=OLD.input_hash
        BEGIN SELECT RAISE(ABORT,'Immutable country source identity'); END""",
        """CREATE TRIGGER country_inference_terminal_no_update
        BEFORE UPDATE ON country_inference_items
        WHEN OLD.status IN ('accepted','unknown','failed','stale')
        BEGIN SELECT RAISE(ABORT,'Terminal country checkpoint'); END""",
        """CREATE TRIGGER country_inference_items_no_delete
        BEFORE DELETE ON country_inference_items
        BEGIN SELECT RAISE(ABORT,'Country checkpoint history'); END""",
        """CREATE TRIGGER country_inference_identity_no_update
        BEFORE UPDATE ON country_inference_runs
        WHEN NEW.configuration_hash!=OLD.configuration_hash
            OR NEW.configuration_json!=OLD.configuration_json
            OR NEW.run_id!=OLD.run_id OR NEW.created_at!=OLD.created_at
        BEGIN SELECT RAISE(ABORT,'Immutable country configuration'); END""",
        """CREATE VIEW country_inference_current AS
        SELECT i.* FROM country_inference_items i
        JOIN postings p ON p.posting_id=i.posting_id AND p.content_hash=i.listing_hash
        LEFT JOIN posting_contents c ON c.posting_id=i.posting_id
        WHERE c.content_hash IS i.content_hash""",
    )
    for statement in statements:
        connection.execute(statement)
    connection.executemany("INSERT INTO country_codes VALUES (?,?)", COUNTRIES.items())
