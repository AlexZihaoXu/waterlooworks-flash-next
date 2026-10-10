"""Public local API contracts. No authentication secrets or raw source responses."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from waterlooworks_flash_next.content import StoredPostingContent
from waterlooworks_flash_next.database import StoredListing


class Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        hide_input_in_errors=True,
        json_schema_serialization_defaults_required=True,
    )


class ErrorResponse(Contract):
    detail: str


class RefreshRequest(Contract):
    """An explicit empty object starts one guarded listing-then-content refresh."""


class CompensationRate(Contract):
    bound_type: Literal["fixed", "range", "minimum", "maximum"]
    minimum: str | None
    maximum: str | None
    minimum_inclusive: bool | None
    maximum_inclusive: bool | None
    currency: str | None
    period: Literal["hour", "day", "week", "month", "year"] | None
    hours_per_week: str | None
    payout_frequency: Literal["weekly", "biweekly", "semi-monthly", "monthly"] | None
    eligibility: str | None
    source_field: str
    evidence_text: str


class CompensationSort(Contract):
    needs_review: bool = False
    source_rates: list[CompensationRate] = Field(default_factory=list)
    currency: str | None
    period: Literal["hour", "day", "week", "month", "year"] | None
    minimum: str | None
    maximum: str | None
    average: str | None
    estimated: bool = False
    assumed_hours: bool = False
    annualized: bool = False
    hours_per_week: list[str] = Field(default_factory=list)


class CountryEvidence(Contract):
    source_kind: Literal["listing", "content"]
    source_field: str
    start: int
    end: int
    text: str = Field(description="Location context, not proof of the prediction.")


class CountryInference(Contract):
    state: Literal[
        "not_requested", "pending", "accepted", "unknown", "failed", "stale"
    ] = "not_requested"
    country_code: str | None = None
    country_name: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    provenance: Literal["model_inference"] = "model_inference"
    reason: str | None = None
    run_id: str | None = None
    origin_run_id: str | None = None
    model_id: str | None = None
    prompt_version: str | None = None
    configuration_hash: str | None = None
    evidence: list[CountryEvidence] = Field(default_factory=list)


class PostingResponse(Contract):
    posting_id: str
    listing: StoredListing
    first_seen_at: str
    last_seen_at: str
    location: str | None
    work_arrangement: Literal["Remote", "Hybrid", "In-person"] | None = None
    country: None = Field(
        default=None, description="Unverified; never inferred from city."
    )
    country_inference: CountryInference = Field(default_factory=CountryInference)
    targeted_disciplines: list[str] = Field(default_factory=list)
    compensation_sort: CompensationSort | None = None


class JobDetailResponse(Contract):
    country_inference: CountryInference = Field(default_factory=CountryInference)
    posting_id: str
    listing: StoredListing
    content: StoredPostingContent | None
    content_observed_at: str | None


AnalysisState = Literal["recognized", "unknown", "needs_analysis"]


class AnalysisEvidence(Contract):
    source_kind: Literal["listing", "content"]
    source_field: str
    source_present: bool
    source_hash: str | None
    source_value: str | None


class AnalysisValue(Contract):
    state: AnalysisState
    value: str | int | bool | None
    evidence: AnalysisEvidence | None
    unknown_values: list[str] = Field(default_factory=list)


class SkillAnalysis(Contract):
    name: str
    requirement: Literal["required", "preferred", "unspecified"]
    source_field: str
    evidence_text: str


class ApplicationLink(Contract):
    url: str
    classification: Literal[
        "required", "optional", "not_required", "uncertain", "informational"
    ]
    source_field: str
    evidence_text: str


ExtractionState = Literal[
    "accepted",
    "partial",
    "empty",
    "rejected",
    "failed",
    "stale",
    "pending",
    "not_requested",
]


class ExtractionStatus(Contract):
    state: ExtractionState = "not_requested"
    run_id: str | None = None
    configuration_hash: str | None = None
    origin_run_ids: list[str] = Field(default_factory=list)
    reused_slices: int = 0
    accepted_slices: int = 0
    total_slices: int = 0


class ExtractedKeyword(Contract):
    name: str
    source_field: str
    evidence_text: str


class PostingAnalysisResponse(Contract):
    posting_id: str
    status: Literal["available", "unavailable"]
    unavailable_reason: Literal["no_current_projection", "stale_projection"] | None
    projection_created_at: str | None
    producer: str | None
    model_status: Literal["available", "quality_review", "rejected", "not_requested"]
    model_producer: str | None = None
    model_error_category: str | None
    salary_extraction: ExtractionStatus = Field(default_factory=ExtractionStatus)
    keyword_extraction: ExtractionStatus = Field(default_factory=ExtractionStatus)
    keywords: list[ExtractedKeyword] = Field(default_factory=list)
    compensation_needs_review: bool = False
    arrangement: AnalysisValue | None
    city: AnalysisValue | None
    province: AnalysisValue | None
    country: AnalysisValue | None
    levels: list[str]
    levels_state: AnalysisState | None
    work_term: AnalysisValue | None
    duration: AnalysisValue | None
    skills_state: AnalysisState | None
    skills: list[SkillAnalysis]
    compensation_state: AnalysisState | None
    compensation_reason: str | None
    compensation: list[CompensationRate]
    application_state: AnalysisState | None
    external_application_required: bool | None
    application_route: (
        Literal["waterlooworks_and_employer", "employer", "unknown"] | None
    )
    employer_links: list[ApplicationLink]
    waterlooworks_url: str


class PostingObservation(Contract):
    observed_at: str
    timestamp_provenance: Literal[
        "response_received",
        "persistence_fallback",
        "legacy_persistence",
        "legacy_baseline",
    ]
    application_count: int | None
    application_count_raw: JsonValue = None
    openings: int | None
    openings_raw: JsonValue = None
    deadline: JsonValue = None
    content_hash: str


class PostingHistoryResponse(Contract):
    posting_id: str
    observations: list[PostingObservation]


class ComparisonRate(Contract):
    hours_per_week: str | None = None
    bound_type: Literal["fixed", "range", "minimum", "maximum"]
    minimum: str | None
    maximum: str | None
    minimum_inclusive: bool | None
    maximum_inclusive: bool | None
    currency: str
    period: Literal["hour", "day", "week", "month", "year"]
    eligibility: str | None


class ComparablePosting(Contract):
    posting_id: str
    title: str | None
    organization: str | None
    shared_skills: list[str]
    shared_levels: list[str]
    shared_work_term: bool
    why_eligible: list[str]
    application_count: int | None
    openings: int | None
    salary: list[ComparisonRate]


class PostingComparisonResponse(Contract):
    posting_id: str
    cohort_description: str
    cohort_count: int
    eligible_count: int
    salary_currency: str | None
    salary_period: str | None
    comparisons: list[ComparablePosting]


class KeywordIndexPosting(Contract):
    posting_id: str
    content_hash: str
    fields: list[str]
    keywords: list[str]
    partial: bool


class KeywordIndexResponse(Contract):
    revision: str
    run_id: str | None
    total_postings: int
    entries: list[KeywordIndexPosting]


class WorkspaceResponse(Contract):
    status: Literal["ready"] = "ready"
    postings: list[PostingResponse]
    manual_refresh_available: bool
    details_available: Literal[False] = False
    compensation_sort_available: bool = True


class ExchangeRateResponse(Contract):
    as_of: str
    cad_per_unit: dict[str, str]


class HealthResponse(Contract):
    status: Literal["ok"] = "ok"
    schema_version: int


class RefreshLog(Contract):
    sequence: int
    at: str
    level: Literal["debug", "info", "warning", "error", "critical"]
    message: str = Field(
        description=(
            "Reviewed log message with scope and safe arguments; "
            "never a token, Duo code, or raw exception."
        )
    )


class RefreshSnapshot(Contract):
    run_id: str | None = None
    status: Literal["idle", "running", "completed", "failed", "interrupted"] = "idle"
    stage: str = "idle"
    pages_completed: int = Field(default=0, ge=0)
    total_pages: int | None = Field(default=None, ge=1)
    records_received: int = Field(default=0, ge=0)
    unique_postings: int = Field(default=0, ge=0)
    duplicates: int = Field(default=0, ge=0)
    retry_after: int = Field(default=0, ge=0)
    details_completed: int = Field(default=0, ge=0)
    details_total: int = Field(default=0, ge=0)
    detail_requests: int = Field(default=0, ge=0)
    detail_retries: int = Field(default=0, ge=0)
    detail_bursts: int = Field(default=0, ge=0)
    detail_wait_seconds: float = Field(default=0, ge=0)
    detail_limited: bool = False
    analysis_job_id: str | None = None
    analysis_total: int = Field(default=0, ge=0)
    analysis_completed: int = Field(default=0, ge=0)
    analysis_accepted: int = Field(default=0, ge=0)
    analysis_rejected: int = Field(default=0, ge=0)
    analysis_failed: int = Field(default=0, ge=0)
    analysis_skipped: int = Field(default=0, ge=0)
    analysis_cache_hits: int = Field(default=0, ge=0)
    analysis_provider: str | None = Field(
        default=None,
        max_length=300,
        description="Active AI access mode and model label while extracting.",
    )
    eta_seconds: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Latest collector-emitted ETA, not a live countdown; null when unestimated."
        ),
    )
    logs: list[RefreshLog] = Field(
        default_factory=list, description="Bounded to the latest 200 sanitized events."
    )


class RefreshAccepted(Contract):
    run_id: str
    status: Literal["running"] = "running"
