"""Intentional offline parsing and transactional v7 normalized persistence."""

import argparse
import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

from pydantic import JsonValue, ValidationError

from waterlooworks_flash_next.application_parser import (
    ApplicationResult,
    parse_application_instructions,
)
from waterlooworks_flash_next.compact_codec import HistoryError
from waterlooworks_flash_next.compact_history import revision_snapshot
from waterlooworks_flash_next.compensation_parser import (
    CompensationResult,
    parse_compensation,
)
from waterlooworks_flash_next.content import StoredPostingContent
from waterlooworks_flash_next.database import StoredListing, open_database
from waterlooworks_flash_next.parser_models import (
    PARSER_VERSION,
    Requirement,
    SourceEvidence,
    SourceSpan,
)
from waterlooworks_flash_next.skill_parser import SkillsResult, parse_skills
from waterlooworks_flash_next.structured_fields import StructuredFields, parse_fields

PRODUCER_NAME = "deterministic-structured-parsers"
PRODUCER_VERSION = "normalized-storage-v1"


class NormalizationError(Exception):
    """Safe offline normalization failure without source values or SQL parameters."""


@dataclass(frozen=True)
class NormalizationBatchResult:
    postings_seen: int
    projections_created: int
    projections_reused: int


@dataclass(frozen=True)
class _SourceRevision:
    posting_id: str
    listing_revision_id: int
    content_revision_id: int
    listing_hash: str
    content_hash: str
    listing: StoredListing
    content: StoredPostingContent


def decimal_to_storage(value: Decimal) -> str:
    """Return exact canonical nonnegative base-10 text without exponent notation."""
    if not value.is_finite() or value < 0:
        raise NormalizationError("Decimal value is unsupported.")
    if value == 0:
        return "0"
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int) or value.adjusted() > 63 or exponent < -63:
        raise NormalizationError("Decimal value is unsupported.")
    result = format(value, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    if len(result) > 64:
        raise NormalizationError("Decimal value is unsupported.")
    return result


def decimal_from_storage(value: str) -> Decimal:
    """Read exact canonical Decimal text and reject alternate encodings."""
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise NormalizationError("Decimal value is unsupported.") from None
    if decimal_to_storage(result) != value:
        raise NormalizationError("Decimal value is not canonical.")
    return result


def _current_source(connection: sqlite3.Connection, posting_id: str) -> _SourceRevision:
    row = connection.execute(
        """SELECT p.content_hash,pc.content_hash,
                  (SELECT max(revision_id) FROM posting_revisions
                   WHERE posting_id=p.posting_id),
                  (SELECT max(revision_id) FROM posting_content_revisions
                   WHERE posting_id=p.posting_id)
           FROM postings p JOIN posting_contents pc USING(posting_id)
           WHERE p.posting_id=?""",
        (posting_id,),
    ).fetchone()
    if row is None or row[2] is None or row[3] is None:
        raise NormalizationError("Current source revisions are unavailable.")
    listing_revision_id, content_revision_id = int(row[2]), int(row[3])
    listing_hash, content_hash = str(row[0]), str(row[1])
    listing_identity = connection.execute(
        "SELECT content_hash FROM posting_revisions "
        "WHERE revision_id=? AND posting_id=?",
        (listing_revision_id, posting_id),
    ).fetchone()
    content_identity = connection.execute(
        "SELECT content_hash FROM posting_content_revisions "
        "WHERE revision_id=? AND posting_id=?",
        (content_revision_id, posting_id),
    ).fetchone()
    if (
        listing_identity is None
        or content_identity is None
        or listing_identity[0] != listing_hash
        or content_identity[0] != content_hash
    ):
        raise NormalizationError("Current source revision identity is inconsistent.")
    try:
        listing = StoredListing.model_validate_json(
            revision_snapshot(connection, "listing", listing_revision_id)
        )
        content = StoredPostingContent.model_validate_json(
            revision_snapshot(connection, "content", content_revision_id)
        )
    except (HistoryError, ValidationError):
        raise NormalizationError(
            "Current source evidence could not be restored."
        ) from None
    if listing.id != posting_id or content.id != posting_id:
        raise NormalizationError("Current source posting identity is inconsistent.")
    return _SourceRevision(
        posting_id=posting_id,
        listing_revision_id=listing_revision_id,
        content_revision_id=content_revision_id,
        listing_hash=listing_hash,
        content_hash=content_hash,
        listing=listing,
        content=content,
    )


def _insert_evidence(
    connection: sqlite3.Connection,
    projection_id: int,
    field_key: str,
    evidence: SourceEvidence,
) -> None:
    connection.execute(
        """INSERT INTO normalized_field_evidence
           (projection_id,field_key,source_kind,source_field,source_present,source_hash)
           VALUES(?,?,?,?,?,?)""",
        (
            projection_id,
            field_key,
            evidence.source,
            evidence.field,
            int(evidence.present),
            evidence.source_hash,
        ),
    )


def _insert_fixed_fields(
    connection: sqlite3.Connection,
    projection_id: int,
    fixed: StructuredFields,
) -> None:
    term = fixed.work_term.value
    duration = fixed.duration.value
    connection.execute(
        """INSERT INTO normalized_fixed_fields
           (projection_id,arrangement_state,arrangement,city_state,city,
            province_state,province,country_state,country,levels_state,
            work_term_state,work_term_year,work_term_season,duration_state,
            duration_count,duration_unit,duration_requirement,job_type_state,
            job_type,targeted_disciplines_state,application_count_state,
            application_count,openings_state,openings,deadline_state,deadline_utc)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            projection_id,
            fixed.arrangement.state,
            fixed.arrangement.value,
            fixed.city.state,
            fixed.city.value,
            fixed.province.state,
            fixed.province.value,
            fixed.country.state,
            fixed.country.value,
            fixed.levels.state,
            fixed.work_term.state,
            term.year if term else None,
            term.season if term else None,
            fixed.duration.state,
            duration.count if duration else None,
            duration.unit if duration else None,
            duration.requirement if duration else None,
            fixed.job_type.state,
            fixed.job_type.value,
            fixed.targeted_disciplines.state,
            fixed.application_count.state,
            fixed.application_count.value,
            fixed.openings.state,
            fixed.openings.value,
            fixed.deadline.state,
            fixed.deadline.value.isoformat() if fixed.deadline.value else None,
        ),
    )
    parsed_fields = (
        ("arrangement", fixed.arrangement),
        ("city", fixed.city),
        ("province", fixed.province),
        ("country", fixed.country),
        ("levels", fixed.levels),
        ("work_term", fixed.work_term),
        ("duration", fixed.duration),
        ("job_type", fixed.job_type),
        ("targeted_disciplines", fixed.targeted_disciplines),
        ("application_count", fixed.application_count),
        ("openings", fixed.openings),
        ("deadline", fixed.deadline),
    )
    for field_key, parsed in parsed_fields:
        _insert_evidence(connection, projection_id, field_key, parsed.evidence)
        connection.executemany(
            "INSERT INTO normalized_field_unknown_values "
            "(projection_id,field_key,ordinal,value) VALUES(?,?,?,?)",
            (
                (projection_id, field_key, ordinal, value)
                for ordinal, value in enumerate(parsed.unknown_values)
            ),
        )
    connection.executemany(
        "INSERT INTO normalized_levels(projection_id,ordinal,level) VALUES(?,?,?)",
        (
            (projection_id, ordinal, level)
            for ordinal, level in enumerate(fixed.levels.value or ())
        ),
    )
    connection.executemany(
        "INSERT INTO normalized_targeted_disciplines"
        "(projection_id,ordinal,discipline) VALUES(?,?,?)",
        (
            (projection_id, ordinal, discipline)
            for ordinal, discipline in enumerate(fixed.targeted_disciplines.value or ())
        ),
    )


def _insert_skill_mentions(
    connection: sqlite3.Connection, projection_id: int, skills: SkillsResult
) -> None:
    mentions: list[
        tuple[str, Requirement, str | None, SourceSpan, SourceSpan | None]
    ] = [
        (
            match.skill,
            match.requirement,
            None,
            match.evidence,
            match.requirement_evidence,
        )
        for match in skills.matches
    ]
    mentions.extend(
        (excluded.skill, "unspecified", excluded.reason, excluded.evidence, None)
        for excluded in skills.excluded
    )
    for ordinal, (skill, requirement, excluded, evidence, qualifier) in enumerate(
        mentions
    ):
        connection.execute(
            "INSERT INTO normalized_skills(canonical_name) VALUES(?) "
            "ON CONFLICT(canonical_name) DO NOTHING",
            (skill,),
        )
        skill_id = connection.execute(
            "SELECT skill_id FROM normalized_skills WHERE canonical_name=?", (skill,)
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO normalized_skill_mentions
               (projection_id,skill_id,ordinal,requirement,excluded_reason,
                source_field,source_start,source_end,requirement_start,
                requirement_end,source_hash)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                projection_id,
                skill_id,
                ordinal,
                requirement,
                excluded,
                evidence.field,
                evidence.start,
                evidence.end,
                qualifier.start if qualifier else None,
                qualifier.end if qualifier else None,
                evidence.source_hash,
            ),
        )


def _insert_compensation(
    connection: sqlite3.Connection,
    projection_id: int,
    compensation: CompensationResult,
) -> None:
    _insert_evidence(connection, projection_id, "compensation", compensation.evidence)
    amount, span = compensation.value, compensation.amount_span
    if amount is None or span is None:
        return
    connection.execute(
        """INSERT INTO normalized_compensation_rates
           (projection_id,ordinal,bound_type,minimum_decimal,maximum_decimal,
            minimum_inclusive,maximum_inclusive,currency,period,
            hours_per_week_decimal,payout_frequency,source_field,source_start,
            source_end,source_hash)
           VALUES(?,0,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            projection_id,
            amount.bound_type,
            decimal_to_storage(amount.minimum) if amount.minimum is not None else None,
            decimal_to_storage(amount.maximum) if amount.maximum is not None else None,
            int(amount.minimum_inclusive)
            if amount.minimum_inclusive is not None
            else None,
            int(amount.maximum_inclusive)
            if amount.maximum_inclusive is not None
            else None,
            amount.currency,
            amount.period,
            decimal_to_storage(amount.hours_per_week)
            if amount.hours_per_week is not None
            else None,
            amount.payout_frequency,
            span.field,
            span.start,
            span.end,
            span.source_hash,
        ),
    )


def _insert_application(
    connection: sqlite3.Connection,
    projection_id: int,
    application: ApplicationResult,
) -> None:
    _insert_evidence(
        connection, projection_id, "application_method", application.application_method
    )
    spans = list(application.instruction_evidence)
    span_keys = {(span.field, span.start, span.end) for span in spans}
    for link in application.external_application_links:
        key = (link.instruction.field, link.instruction.start, link.instruction.end)
        if key not in span_keys:
            spans.append(link.instruction)
            span_keys.add(key)
    instruction_ordinals = {
        (span.field, span.start, span.end): ordinal
        for ordinal, span in enumerate(spans)
    }
    connection.executemany(
        """INSERT INTO normalized_application_instructions
           (projection_id,ordinal,source_field,source_start,source_end,source_hash)
           VALUES(?,?,?,?,?,?)""",
        (
            (
                projection_id,
                ordinal,
                span.field,
                span.start,
                span.end,
                span.source_hash,
            )
            for ordinal, span in enumerate(spans)
        ),
    )
    connection.executemany(
        """INSERT INTO normalized_application_links
           (projection_id,ordinal,url,classification,source_field,source_start,
            source_end,instruction_ordinal,source_hash)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            (
                projection_id,
                ordinal,
                link.url,
                link.classification,
                link.evidence.field,
                link.evidence.start,
                link.evidence.end,
                instruction_ordinals[
                    (
                        link.instruction.field,
                        link.instruction.start,
                        link.instruction.end,
                    )
                ],
                link.evidence.source_hash,
            )
            for ordinal, link in enumerate(application.external_application_links)
        ),
    )


def _persist_projection(
    connection: sqlite3.Connection,
    source: _SourceRevision,
    fixed: StructuredFields,
    skills: SkillsResult,
    compensation: CompensationResult,
    application: ApplicationResult,
) -> bool:
    created_at = connection.execute(
        "SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now')"
    ).fetchone()[0]
    inserted = connection.execute(
        """INSERT INTO analysis_projections
           (posting_id,listing_revision_id,content_revision_id,listing_hash,
            content_hash,producer_kind,producer_name,producer_version,
            parser_version,created_at)
           VALUES(?,?,?,?,?,'deterministic_parser',?,?,?,?)
           ON CONFLICT(posting_id,listing_revision_id,content_revision_id,
               producer_kind,producer_name,producer_version,parser_version)
           DO NOTHING""",
        (
            source.posting_id,
            source.listing_revision_id,
            source.content_revision_id,
            source.listing_hash,
            source.content_hash,
            PRODUCER_NAME,
            PRODUCER_VERSION,
            PARSER_VERSION,
            created_at,
        ),
    )
    row = connection.execute(
        """SELECT projection_id FROM analysis_projections
           WHERE posting_id=? AND listing_revision_id=? AND content_revision_id=?
             AND producer_kind='deterministic_parser' AND producer_name=?
             AND producer_version=? AND parser_version=?""",
        (
            source.posting_id,
            source.listing_revision_id,
            source.content_revision_id,
            PRODUCER_NAME,
            PRODUCER_VERSION,
            PARSER_VERSION,
        ),
    ).fetchone()
    if row is None:
        raise NormalizationError("Normalized projection identity was not retained.")
    projection_id = int(row[0])
    created = inserted.rowcount == 1
    if created:
        _insert_fixed_fields(connection, projection_id, fixed)
        _insert_skill_mentions(connection, projection_id, skills)
        _insert_compensation(connection, projection_id, compensation)
        _insert_application(connection, projection_id, application)
        connection.execute(
            """INSERT INTO normalized_projection_results
               (projection_id,skills_state,skills_coverage,compensation_state,
                compensation_reason,application_state,
                external_application_required,application_route)
               VALUES(?,?,?,?,?,?,?,?)""",
            (
                projection_id,
                skills.state,
                skills.coverage,
                compensation.state,
                compensation.reason,
                application.state,
                int(application.external_application_required)
                if application.external_application_required is not None
                else None,
                application.application_route,
            ),
        )
    connection.execute(
        """INSERT INTO posting_projection_current
           (posting_id,deterministic_projection_id,updated_at) VALUES(?,?,?)
           ON CONFLICT(posting_id) DO UPDATE SET
             deterministic_projection_id=excluded.deterministic_projection_id,
             updated_at=excluded.updated_at
           WHERE deterministic_projection_id<>excluded.deterministic_projection_id""",
        (source.posting_id, projection_id, created_at),
    )
    return created


def normalize_saved_posting(connection: sqlite3.Connection, posting_id: str) -> bool:
    """Parse and atomically persist one saved posting's exact current revisions.

    Returns true only when a new immutable projection was created. This function owns
    its transaction and performs no collection, authentication, network or model work.
    """
    if connection.in_transaction:
        raise NormalizationError("Normalization requires its own transaction.")
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            source = _current_source(connection, posting_id)
            listing_values: dict[str, JsonValue] = source.listing.model_dump(
                exclude_unset=True
            )
            fields: dict[str, JsonValue] = dict(source.content.fields)
            fixed = parse_fields(
                fields,
                listing_values,
                content_hash=source.content_hash,
                listing_hash=source.listing_hash,
            )
            skills = parse_skills(fields, source_hash=source.content_hash)
            compensation = parse_compensation(
                fields.get("Compensation and Benefits:"),
                source_hash=source.content_hash,
                present="Compensation and Benefits:" in fields,
            )
            application = parse_application_instructions(
                fields, source_hash=source.content_hash
            )
            return _persist_projection(
                connection, source, fixed, skills, compensation, application
            )
    except NormalizationError:
        raise
    except (sqlite3.Error, HistoryError, ValidationError, ValueError, TypeError):
        raise NormalizationError(
            "Normalized projection persistence failed; diagnostics suppressed."
        ) from None


def normalize_saved_postings(
    connection: sqlite3.Connection, posting_ids: list[str] | None = None
) -> NormalizationBatchResult:
    """Run the resumable offline batch, committing each posting independently."""
    if connection.in_transaction:
        raise NormalizationError("Normalization requires its own transaction.")
    if posting_ids is None:
        posting_ids = [
            str(row[0])
            for row in connection.execute(
                "SELECT posting_id FROM posting_contents ORDER BY posting_id"
            )
        ]
    if len(posting_ids) != len(set(posting_ids)):
        raise NormalizationError("Normalization posting IDs must be unique.")
    created = 0
    for posting_id in posting_ids:
        created += int(normalize_saved_posting(connection, posting_id))
    return NormalizationBatchResult(
        postings_seen=len(posting_ids),
        projections_created=created,
        projections_reused=len(posting_ids) - created,
    )


def restore_field_evidence(
    connection: sqlite3.Connection, projection_id: int, field_key: str
) -> SourceEvidence:
    """Reconstruct one fixed raw value from its immutable compact source revision."""
    row = connection.execute(
        """SELECT e.source_kind,e.source_field,e.source_present,e.source_hash,
                  a.listing_revision_id,a.content_revision_id,
                  a.listing_hash,a.content_hash
           FROM normalized_field_evidence e
           JOIN analysis_projections a USING(projection_id)
           WHERE e.projection_id=? AND e.field_key=?""",
        (projection_id, field_key),
    ).fetchone()
    if row is None:
        raise NormalizationError("Normalized field evidence is unavailable.")
    source_kind: Literal["listing", "content"] = row[0]
    revision_id = int(row[4] if source_kind == "listing" else row[5])
    expected_hash = str(row[6] if source_kind == "listing" else row[7])
    if row[3] != expected_hash:
        raise NormalizationError("Normalized field evidence hash is inconsistent.")
    try:
        payload = json.loads(revision_snapshot(connection, source_kind, revision_id))
        values = payload if source_kind == "listing" else payload["fields"]
        present = row[1] in values
        raw = values.get(row[1])
    except (HistoryError, json.JSONDecodeError, KeyError, TypeError, AttributeError):
        raise NormalizationError(
            "Normalized field evidence could not be restored."
        ) from None
    if present != bool(row[2]):
        raise NormalizationError("Normalized field evidence presence is inconsistent.")
    return SourceEvidence(
        source=source_kind,
        field=row[1],
        present=present,
        raw=raw,
        source_hash=expected_hash,
    )


def restore_source_span(
    connection: sqlite3.Connection,
    projection_id: int,
    source_field: str,
    start: int,
    end: int,
    source_hash: str,
) -> SourceSpan:
    """Restore and validate a bounded content span without storing its source text."""
    row = connection.execute(
        "SELECT content_revision_id,content_hash FROM analysis_projections "
        "WHERE projection_id=?",
        (projection_id,),
    ).fetchone()
    if row is None or row[1] != source_hash:
        raise NormalizationError("Normalized span source is unavailable.")
    try:
        content = StoredPostingContent.model_validate_json(
            revision_snapshot(connection, "content", int(row[0]))
        )
        text = content.fields[source_field]
        span_text = text[start:end]
        span = SourceSpan(
            field=source_field,
            start=start,
            end=end,
            text=span_text,
            source_hash=source_hash,
        )
    except (HistoryError, ValidationError, KeyError, ValueError):
        raise NormalizationError(
            "Normalized span evidence could not be restored."
        ) from None
    if not span.matches(text):
        raise NormalizationError("Normalized span evidence is inconsistent.")
    return span


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Offline deterministic normalization of already saved postings."
    )
    parser.add_argument("database", type=Path)
    parser.add_argument("--posting-id", action="append", dest="posting_ids")
    args = parser.parse_args()
    connection = open_database(args.database)
    try:
        result = normalize_saved_postings(connection, args.posting_ids)
    finally:
        connection.close()
    print(
        json.dumps(
            {
                "postings_seen": result.postings_seen,
                "projections_created": result.projections_created,
                "projections_reused": result.projections_reused,
            },
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
