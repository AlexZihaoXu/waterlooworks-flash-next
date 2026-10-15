"""Read-only, source-fresh projections, observations, and transparent comparisons."""

import json
import sqlite3
from typing import cast

from waterlooworks_flash_next.api_models import (
    AnalysisEvidence,
    AnalysisState,
    AnalysisValue,
    ApplicationLink,
    ComparablePosting,
    ComparisonRate,
    CompensationRate,
    PostingAnalysisResponse,
    PostingComparisonResponse,
    PostingHistoryResponse,
    PostingObservation,
    SkillAnalysis,
)
from waterlooworks_flash_next.content import StoredPostingContent
from waterlooworks_flash_next.database import StoredListing
from waterlooworks_flash_next.inference_compatibility import (
    trusted_model_projection,
    trusted_model_projections,
)
from waterlooworks_flash_next.observations import observation_series
from waterlooworks_flash_next.source_enrichment import (
    SourceEnrichment,
    SourcePay,
    legacy_amount_span,
    merge_compensation,
    read_source_enrichment,
)

WW_JOB_BOARD = "https://waterlooworks.uwaterloo.ca/myAccount/co-op/full/jobs.htm"


# Latest deterministic projection of the current saved description. Listing
# revisions (application counts) churn every pass and never hide saved pay.
_CURRENT_PROJECTION = """FROM posting_projection_current pointer
           JOIN analysis_projections a
             ON a.projection_id=pointer.deterministic_projection_id
           JOIN normalized_fixed_fields f USING(projection_id)
           JOIN normalized_projection_results r USING(projection_id)
           JOIN postings p USING(posting_id)
           JOIN posting_contents c USING(posting_id)
           WHERE a.producer_kind='deterministic_parser'
             AND a.content_hash=c.content_hash
             AND a.content_revision_id=(SELECT max(revision_id)
                 FROM posting_content_revisions WHERE posting_id=a.posting_id)"""


def _projection(connection: sqlite3.Connection, posting_id: str) -> sqlite3.Row | None:
    return cast(
        sqlite3.Row | None,
        connection.execute(
            """SELECT a.*, f.*, r.skills_state,r.compensation_state,
                  r.compensation_reason,r.application_state,
                  r.external_application_required,r.application_route,
                  p.listing_json,c.content_json """
            + _CURRENT_PROJECTION
            + " AND pointer.posting_id=?",
            (posting_id,),
        ).fetchone(),
    )


def _source_value(
    evidence: sqlite3.Row,
    listing: StoredListing,
    content: StoredPostingContent,
) -> str | None:
    if evidence["source_kind"] == "content":
        return content.fields.get(evidence["source_field"])
    value = listing.model_dump().get(evidence["source_field"])
    if value is None:
        return None
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _analysis_value(
    connection: sqlite3.Connection,
    projection_id: int,
    key: str,
    state: AnalysisState,
    value: str | int | bool | None,
    listing: StoredListing,
    content: StoredPostingContent,
) -> AnalysisValue:
    evidence = connection.execute(
        "SELECT * FROM normalized_field_evidence WHERE projection_id=? AND field_key=?",
        (projection_id, key),
    ).fetchone()
    unknowns = [
        row[0]
        for row in connection.execute(
            "SELECT value FROM normalized_field_unknown_values "
            "WHERE projection_id=? AND field_key=? ORDER BY ordinal",
            (projection_id, key),
        )
    ]
    source = (
        AnalysisEvidence(
            source_kind=evidence["source_kind"],
            source_field=evidence["source_field"],
            source_present=bool(evidence["source_present"]),
            source_hash=evidence["source_hash"],
            source_value=_source_value(evidence, listing, content),
        )
        if evidence is not None
        else None
    )
    return AnalysisValue(
        state=state, value=value, evidence=source, unknown_values=unknowns
    )


def _span_text(
    content: StoredPostingContent, content_hash: str, row: sqlite3.Row
) -> str:
    source = content.fields.get(row["source_field"], "")
    start, end = row["source_start"], row["source_end"]
    if row["source_hash"] != content_hash or not (0 <= start < end <= len(source)):
        return ""
    return source[start:end]


def _merged_compensation(
    pay_rows: list[sqlite3.Row],
    content: StoredPostingContent,
    content_hash: str,
    source_pay: list[SourcePay],
) -> tuple[list[CompensationRate], bool]:
    """Source-anchored saved rates reconciled with saved extraction pay."""
    legacy: list[SourcePay] = []
    for item in pay_rows:
        evidence = _span_text(content, content_hash, item)
        if not evidence:
            continue
        rate = CompensationRate(
            bound_type=item["bound_type"],
            minimum=item["minimum_decimal"],
            maximum=item["maximum_decimal"],
            minimum_inclusive=bool(item["minimum_inclusive"])
            if item["minimum_inclusive"] is not None
            else None,
            maximum_inclusive=bool(item["maximum_inclusive"])
            if item["maximum_inclusive"] is not None
            else None,
            currency=item["currency"],
            period=item["period"],
            hours_per_week=item["hours_per_week_decimal"],
            payout_frequency=item["payout_frequency"],
            eligibility=item["eligibility"],
            source_field=item["source_field"],
            evidence_text=evidence,
        )
        legacy.append(
            SourcePay(
                rate,
                *legacy_amount_span(rate, item["source_start"], item["source_end"]),
            )
        )
    return merge_compensation(legacy, source_pay)


def read_compensations(
    connection: sqlite3.Connection,
) -> dict[str, tuple[list[CompensationRate], bool]]:
    """Every saved posting's merged pay, as ``read_analysis`` would report it.

    A fixed number of set-based queries; content is parsed only for postings that
    have saved rates needing evidence spans. Caller owns the read transaction.
    """
    source = read_source_enrichment(connection)
    current = {
        row["posting_id"]: row
        for row in connection.execute(
            "SELECT a.posting_id,a.projection_id,a.content_hash,"
            "r.compensation_state " + _CURRENT_PROJECTION
        )
    }
    models = trusted_model_projections(connection) if current else {}
    pay_projection: dict[str, int] = {}
    for posting_id, row in current.items():
        model = models.get(posting_id)
        model_pay = model is not None and row["compensation_state"] == "needs_analysis"
        pay_projection[posting_id] = (
            model["projection_id"]
            if model is not None and model_pay
            else row["projection_id"]
        )
    pay_rows: dict[int, list[sqlite3.Row]] = {}
    if pay_projection:
        for item in connection.execute(
            "SELECT * FROM normalized_compensation_rates WHERE projection_id IN "
            "(SELECT value FROM json_each(?)) ORDER BY projection_id,ordinal",
            (json.dumps(sorted(set(pay_projection.values()))),),
        ):
            pay_rows.setdefault(item["projection_id"], []).append(item)
    paid = sorted(
        posting_id
        for posting_id, projection_id in pay_projection.items()
        if projection_id in pay_rows
    )
    contents: dict[str, StoredPostingContent] = {}
    if paid:
        for item in connection.execute(
            "SELECT posting_id,content_json FROM posting_contents WHERE posting_id IN "
            "(SELECT value FROM json_each(?))",
            (json.dumps(paid),),
        ):
            contents[item[0]] = StoredPostingContent.model_validate_json(item[1])
    result: dict[str, tuple[list[CompensationRate], bool]] = {}
    for posting_id in sorted(
        set(current) | {id_ for id_, entry in source.items() if entry.pay}
    ):
        entry = source.get(posting_id, SourceEnrichment())
        row = current.get(posting_id)
        rows = pay_rows.get(pay_projection[posting_id], []) if row is not None else []
        if row is None or not rows:
            # No saved rates: only saved extraction pay can contribute.
            result[posting_id] = merge_compensation([], entry.pay)
        else:
            result[posting_id] = _merged_compensation(
                rows, contents[posting_id], row["content_hash"], entry.pay
            )
    return result


def read_analysis(
    connection: sqlite3.Connection,
    posting_id: str,
    source_enrichment: dict[str, SourceEnrichment] | None = None,
) -> PostingAnalysisResponse:
    exists = connection.execute(
        "SELECT 1 FROM postings WHERE posting_id=?", (posting_id,)
    ).fetchone()
    if exists is None:
        raise KeyError(posting_id)
    source = (
        read_source_enrichment(connection, posting_id)
        if source_enrichment is None
        else source_enrichment
    ).get(posting_id, SourceEnrichment())
    source_rates, source_review = merge_compensation([], source.pay)
    row = _projection(connection, posting_id)
    pointer = connection.execute(
        "SELECT 1 FROM posting_projection_current WHERE posting_id=?", (posting_id,)
    ).fetchone()
    model = (
        trusted_model_projection(connection, posting_id) if row is not None else None
    )
    latest_model = connection.execute(
        """SELECT item.status,item.error_category FROM inference_job_items item
           JOIN inference_jobs job USING(job_id) WHERE item.posting_id=?
           ORDER BY job.created_at DESC LIMIT 1""",
        (posting_id,),
    ).fetchone()
    model_status = (
        "available"
        if model is not None
        else "rejected"
        if latest_model is not None and latest_model[0] in ("rejected", "failed")
        else "quality_review"
        if latest_model is not None
        else "not_requested"
    )
    model_error = latest_model[1] if model_status == "rejected" else None
    empty: dict[str, object] = dict(
        posting_id=posting_id,
        status="unavailable",
        unavailable_reason="stale_projection" if pointer else "no_current_projection",
        projection_created_at=None,
        producer=None,
        model_status=model_status,
        model_producer=(
            f"{model['producer_name']}@{model['producer_version']}"
            if model is not None
            else None
        ),
        model_error_category=model_error,
        salary_extraction=source.salary,
        keyword_extraction=source.keywords_status,
        keywords=source.keywords,
        compensation_needs_review=source_review,
        arrangement=None,
        city=None,
        province=None,
        country=None,
        levels=[],
        levels_state=None,
        work_term=None,
        duration=None,
        skills_state=None,
        skills=[],
        compensation_state="recognized" if source_rates else None,
        compensation_reason="source_validated_extraction" if source_rates else None,
        compensation=source_rates,
        application_state=None,
        external_application_required=None,
        application_route=None,
        employer_links=[],
        waterlooworks_url=WW_JOB_BOARD,
    )
    if row is None:
        return PostingAnalysisResponse.model_validate(empty)
    projection_id = row["projection_id"]
    listing = StoredListing.model_validate_json(row["listing_json"])
    content = StoredPostingContent.model_validate_json(row["content_json"])
    levels = [
        item[0]
        for item in connection.execute(
            "SELECT level FROM normalized_levels WHERE projection_id=? "
            "ORDER BY ordinal",
            (projection_id,),
        )
    ]
    term = (
        f"{row['work_term_season']} {row['work_term_year']}"
        if row["work_term_state"] == "recognized"
        else None
    )
    duration = (
        f"{row['duration_count']} {row['duration_unit']}"
        f" ({row['duration_requirement']})"
        if row["duration_state"] == "recognized"
        else None
    )
    skills = [
        SkillAnalysis(
            name=item["canonical_name"],
            requirement=item["requirement"],
            source_field=item["source_field"],
            evidence_text=_span_text(content, row["content_hash"], item),
        )
        for item in connection.execute(
            """SELECT mention.*,skill.canonical_name
               FROM normalized_skill_mentions mention
               JOIN normalized_skills skill USING(skill_id)
               WHERE mention.projection_id IN (?,?) AND mention.excluded_reason IS NULL
               ORDER BY mention.projection_id,mention.ordinal""",
            (projection_id, model["projection_id"] if model is not None else -1),
        )
        if _span_text(content, row["content_hash"], item)
    ]
    model_pay = model is not None and row["compensation_state"] == "needs_analysis"
    pay_projection_id = (
        model["projection_id"] if model is not None and model_pay else projection_id
    )
    pay_rows = connection.execute(
        "SELECT * FROM normalized_compensation_rates "
        "WHERE projection_id=? ORDER BY ordinal",
        (pay_projection_id,),
    ).fetchall()
    compensation, needs_review = _merged_compensation(
        pay_rows, content, row["content_hash"], source.pay
    )
    empty["compensation_needs_review"] = needs_review
    links = [
        ApplicationLink(
            url=item["url"],
            classification=item["classification"],
            source_field=item["source_field"],
            evidence_text=_span_text(content, row["content_hash"], item),
        )
        for item in connection.execute(
            "SELECT * FROM normalized_application_links "
            "WHERE projection_id=? ORDER BY ordinal",
            (projection_id,),
        )
        if _span_text(content, row["content_hash"], item)
    ]
    empty.update(
        status="available",
        unavailable_reason=None,
        projection_created_at=row["created_at"],
        producer=f"{row['producer_name']}@{row['producer_version']}",
        arrangement=_analysis_value(
            connection,
            projection_id,
            "arrangement",
            row["arrangement_state"],
            row["arrangement"],
            listing,
            content,
        ),
        city=_analysis_value(
            connection,
            projection_id,
            "city",
            row["city_state"],
            row["city"],
            listing,
            content,
        ),
        province=_analysis_value(
            connection,
            projection_id,
            "province",
            row["province_state"],
            row["province"],
            listing,
            content,
        ),
        country=_analysis_value(
            connection,
            projection_id,
            "country",
            row["country_state"],
            row["country"],
            listing,
            content,
        ),
        levels=levels,
        levels_state=row["levels_state"],
        work_term=_analysis_value(
            connection,
            projection_id,
            "work_term",
            row["work_term_state"],
            term,
            listing,
            content,
        ),
        duration=_analysis_value(
            connection,
            projection_id,
            "duration",
            row["duration_state"],
            duration,
            listing,
            content,
        ),
        skills_state="recognized" if skills else row["skills_state"],
        skills=skills,
        compensation_state="needs_analysis"
        if needs_review
        else "recognized"
        if compensation
        else "unknown"
        if model_pay
        else row["compensation_state"],
        compensation_reason="conflicting_source_rates"
        if needs_review
        else "source_validated_extraction"
        if source.pay
        else ("source_validated_model" if compensation else "no_validated_model_rates")
        if model_pay
        else row["compensation_reason"],
        compensation=compensation,
        application_state=row["application_state"],
        external_application_required=bool(row["external_application_required"])
        if row["external_application_required"] is not None
        else None,
        application_route=row["application_route"],
        employer_links=links,
    )
    return PostingAnalysisResponse.model_validate(empty)


def read_history(
    connection: sqlite3.Connection, posting_id: str
) -> PostingHistoryResponse:
    if (
        connection.execute(
            "SELECT 1 FROM postings WHERE posting_id=?", (posting_id,)
        ).fetchone()
        is None
    ):
        raise KeyError(posting_id)
    observations: list[PostingObservation] = []
    for row in observation_series(connection, posting_id):
        listing = StoredListing.model_validate_json(row["listing_json"])
        application_count = listing.application_count
        openings = listing.openings
        observations.append(
            PostingObservation(
                observed_at=row["observed_at"],
                timestamp_provenance=row["timestamp_provenance"],
                application_count=application_count
                if type(application_count) is int and application_count >= 0
                else None,
                application_count_raw=application_count,
                openings=openings if type(openings) is int and openings >= 0 else None,
                openings_raw=openings,
                deadline=listing.deadline,
                content_hash=row["content_hash"],
            )
        )
    return PostingHistoryResponse(posting_id=posting_id, observations=observations)


def _comparison_rates(
    connection: sqlite3.Connection,
    projection_id: int,
    currency: str | None,
    period: str | None,
) -> list[ComparisonRate]:
    if currency is None or period is None:
        return []
    return [
        ComparisonRate(
            bound_type=row["bound_type"],
            minimum=row["minimum_decimal"],
            maximum=row["maximum_decimal"],
            minimum_inclusive=bool(row["minimum_inclusive"])
            if row["minimum_inclusive"] is not None
            else None,
            maximum_inclusive=bool(row["maximum_inclusive"])
            if row["maximum_inclusive"] is not None
            else None,
            currency=row["currency"],
            period=row["period"],
            eligibility=row["eligibility"],
            hours_per_week=row["hours_per_week_decimal"],
        )
        for row in connection.execute(
            "SELECT * FROM normalized_compensation_rates WHERE projection_id=? "
            "AND currency=? AND period=? ORDER BY ordinal",
            (projection_id, currency, period),
        )
    ]


def read_comparisons(
    connection: sqlite3.Connection, posting_id: str
) -> PostingComparisonResponse:
    selected = _projection(connection, posting_id)
    if (
        connection.execute(
            "SELECT 1 FROM postings WHERE posting_id=?", (posting_id,)
        ).fetchone()
        is None
    ):
        raise KeyError(posting_id)
    if selected is None:
        return PostingComparisonResponse(
            posting_id=posting_id,
            cohort_description=(
                "Saved jobs with current deterministic analysis; selected job has none."
            ),
            cohort_count=0,
            eligible_count=0,
            salary_currency=None,
            salary_period=None,
            comparisons=[],
        )
    projection_id = selected["projection_id"]
    selected_skills = {
        row[0]
        for row in connection.execute(
            """SELECT skill.canonical_name FROM normalized_skill_mentions mention
               JOIN normalized_skills skill USING(skill_id)
               WHERE mention.projection_id=? AND mention.excluded_reason IS NULL""",
            (projection_id,),
        )
    }
    selected_levels = {
        row[0]
        for row in connection.execute(
            "SELECT level FROM normalized_levels WHERE projection_id=?",
            (projection_id,),
        )
    }
    selected_term = (
        (selected["work_term_year"], selected["work_term_season"])
        if selected["work_term_state"] == "recognized"
        else None
    )
    selected_rates = connection.execute(
        "SELECT DISTINCT currency,period FROM normalized_compensation_rates "
        "WHERE projection_id=? AND currency IS NOT NULL AND period IS NOT NULL",
        (projection_id,),
    ).fetchall()
    salary_currency, salary_period = (
        (selected_rates[0][0], selected_rates[0][1])
        if len(selected_rates) == 1
        else (None, None)
    )
    candidates = connection.execute(
        """SELECT a.projection_id,a.posting_id,p.title,p.organization,
                  f.application_count,f.openings,f.work_term_year,f.work_term_season,
                  (SELECT group_concat(level,'|') FROM normalized_levels
                   WHERE projection_id=a.projection_id) levels,
                  (SELECT group_concat(skill.canonical_name,'|')
                   FROM normalized_skill_mentions mention
                   JOIN normalized_skills skill USING(skill_id)
                   WHERE mention.projection_id=a.projection_id
                     AND mention.excluded_reason IS NULL) skills
           FROM posting_projection_current pointer
           JOIN analysis_projections a
             ON a.projection_id=pointer.deterministic_projection_id
           JOIN normalized_fixed_fields f USING(projection_id)
           JOIN postings p USING(posting_id) JOIN posting_contents c USING(posting_id)
           WHERE a.posting_id<>? AND a.producer_kind='deterministic_parser'
             AND a.content_hash=c.content_hash
             AND a.content_revision_id=(SELECT max(revision_id)
                 FROM posting_content_revisions WHERE posting_id=a.posting_id)""",
        (posting_id,),
    ).fetchall()
    # Rank first; salary rows are read only for the twelve returned postings.
    eligible: list[tuple[int, str, sqlite3.Row, list[str], list[str], bool]] = []
    for item in candidates:
        skills = set(item["skills"].split("|")) if item["skills"] else set()
        levels = set(item["levels"].split("|")) if item["levels"] else set()
        shared_skills = sorted(selected_skills & skills)
        shared_levels = sorted(selected_levels & levels)
        shared_term = selected_term is not None and selected_term == (
            item["work_term_year"],
            item["work_term_season"],
        )
        if not (shared_skills or shared_levels or shared_term):
            continue
        eligible.append(
            (
                len(shared_skills) + len(shared_levels) + int(shared_term),
                item["posting_id"],
                item,
                shared_skills,
                shared_levels,
                shared_term,
            )
        )
    eligible.sort(key=lambda entry: (-entry[0], entry[1]))
    comparisons = []
    for _, _, item, shared_skills, shared_levels, shared_term in eligible[:12]:
        why = []
        if shared_skills:
            why.append(
                f"{len(shared_skills)} shared skill"
                + ("s" if len(shared_skills) != 1 else "")
            )
        if shared_levels:
            why.append("shared level")
        if shared_term:
            why.append("same work term")
        comparisons.append(
            ComparablePosting(
                posting_id=item["posting_id"],
                title=item["title"],
                organization=item["organization"],
                shared_skills=shared_skills,
                shared_levels=shared_levels,
                shared_work_term=shared_term,
                why_eligible=why,
                application_count=item["application_count"],
                openings=item["openings"],
                salary=_comparison_rates(
                    connection,
                    item["projection_id"],
                    salary_currency,
                    salary_period,
                ),
            )
        )
    return PostingComparisonResponse(
        posting_id=posting_id,
        cohort_description=(
            "Other saved jobs with current deterministic analysis sharing at least "
            "one explicit skill, level, or exact work term."
        ),
        cohort_count=len(candidates),
        eligible_count=len(eligible),
        salary_currency=salary_currency,
        salary_period=salary_period,
        comparisons=comparisons,
    )
