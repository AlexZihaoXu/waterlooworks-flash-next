"""Public local API contracts. No authentication secrets or raw source responses."""

from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SecretStr,
    model_validator,
)

from waterlooworks_flash_next.account_profile import AccountProfile
from waterlooworks_flash_next.auth_challenge import AuthChallengeStatus
from waterlooworks_flash_next.content import StoredPostingContent
from waterlooworks_flash_next.database import StoredListing
from waterlooworks_flash_next.discord_webhooks import DiscordWebhook
from waterlooworks_flash_next.posting_marks import PostingMark, PostingState


class Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        hide_input_in_errors=True,
        json_schema_serialization_defaults_required=True,
    )


class ErrorResponse(Contract):
    detail: str


class RefreshRequest(Contract):
    """Starts one guarded listing-then-content refresh; ``{}`` is the usual body."""

    confirm_recent: bool = Field(
        default=False,
        description=(
            "Admin confirmation to refresh within WW_REFRESH_MIN_INTERVAL_MINUTES "
            "of the previous run's finish. Never waives a ban-risk/rate-limit "
            "block or the rolling 24-hour request budget."
        ),
    )


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


class ScheduledFailureNotice(Contract):
    """Member-safe notice: a scheduled refresh failed (no category or run)."""

    failed: Literal[True] = True
    failed_at: str = Field(description="UTC ISO time the failed slot was due.")


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
    detail_bursts: int = Field(
        default=0,
        ge=0,
        description="Polite rest breaks the shared WaterlooWorks pacing gate took.",
    )
    detail_wait_seconds: float = Field(
        default=0,
        ge=0,
        description="Current scheduled rest/retry wait in seconds.",
    )
    detail_limited: bool = False
    ww_requests: int = Field(
        default=0,
        ge=0,
        description=(
            "WaterlooWorks requests counted for this run: paced requests, retries "
            "and counted browser page loads."
        ),
    )
    ww_request_budget: int | None = Field(
        default=None,
        ge=0,
        description="This run's allowance: min(per-run cap, rolling remaining).",
    )
    pacing_stop: Literal["request_cap", "daily_cap", "ban_risk"] | None = Field(
        default=None,
        description=(
            "Why the run stopped early: a request cap (finished partial, remaining "
            "postings wait for the next refresh) or a ban-risk signal from "
            "WaterlooWorks (failed; refreshes blocked)."
        ),
    )
    error_category: str | None = Field(
        default=None, description="Fixed failure category of a failed run."
    )
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
    next_scheduled_at: str | None = Field(
        default=None,
        description=(
            "UTC ISO time of the next scheduled refresh; null when scheduling is "
            "off, has no times or is inactive on this server."
        ),
    )
    scheduled_failure: ScheduledFailureNotice | None = Field(
        default=None,
        description="Set while scheduling is off after a failed scheduled refresh.",
    )


class RefreshPacingStatus(Contract):
    """Server pacing policy and rolling usage; no credentials or source data."""

    request_cap: int = Field(ge=1, description="WW_REFRESH_REQUEST_CAP per run.")
    daily_request_cap: int = Field(
        ge=1, description="WW_REFRESH_DAILY_REQUEST_CAP per rolling 24 hours."
    )
    min_interval_minutes: int = Field(
        ge=0,
        description=(
            "WW_REFRESH_MIN_INTERVAL_MINUTES after a run finishes: scheduled "
            "refreshes skip within it; a manual refresh asks for confirmation."
        ),
    )
    min_gap_seconds: float = Field(ge=0)
    max_gap_seconds: float = Field(ge=0)
    rest_every_min: int = Field(ge=1)
    rest_every_max: int = Field(ge=1)
    rest_min_seconds: float = Field(ge=0)
    rest_max_seconds: float = Field(ge=0)
    used_24h: int = Field(ge=0, description="Requests counted in the last 24 hours.")
    remaining_24h: int = Field(ge=0)
    next_allowed_at: str | None = Field(
        description=(
            "UTC ISO end of the hard wait (block or rolling budget); null when a "
            "manual refresh may start now, possibly after confirmation."
        )
    )
    last_finished_at: str | None = Field(
        description="UTC ISO finish time of the most recent finished run, if any."
    )
    cooldown_until: str | None = Field(
        description=(
            "UTC ISO end of the minimum interval after the last finish; null when "
            "past. Before it, a manual refresh needs confirm_recent."
        )
    )
    blocked_until: str | None = Field(
        description="Active ban-risk/rate-limit block end (UTC ISO), if any."
    )
    last_pacing_stop: Literal["request_cap", "daily_cap", "ban_risk"] | None = Field(
        description="Why the most recent finished run stopped early, if it did."
    )
    last_error_category: str | None = Field(
        description="Fixed failure category of the most recent finished run."
    )


class AdminRefreshStatus(AuthChallengeStatus):
    """Current Duo challenge plus the WaterlooWorks pacing status (admin only)."""

    pacing: RefreshPacingStatus


class RefreshAccepted(Contract):
    run_id: str
    status: Literal["running"] = "running"


ScheduleOutcome = Literal[
    "claimed",
    "started",
    "skipped_running",
    "skipped_blocked",
    "skipped_disabled",
    "failed_to_start",
]
ScheduleFailureCategory = Literal[
    "sign_in", "authentication", "rate_limit", "redirect", "ban_risk"
]


class ScheduleTimeRequest(Contract):
    """One Toronto wall-clock time, 24-hour HH:MM."""

    model_config = ConfigDict(strict=True)
    time: str = Field(min_length=5, max_length=5, repr=False)


class ScheduleToggleRequest(Contract):
    model_config = ConfigDict(strict=True)
    enabled: bool


class ScheduleSlotView(Contract):
    slot: str = Field(description="Toronto local date and HH:MM.")
    due_at: str = Field(description="UTC ISO instant the slot was due.")
    outcome: ScheduleOutcome
    run_id: str | None
    failure_category: ScheduleFailureCategory | None


class ScheduleFailureView(Contract):
    run_id: str
    slot: str
    failed_at: str = Field(description="UTC ISO instant the failed slot was due.")
    category: ScheduleFailureCategory


class RefreshScheduleView(Contract):
    """Admin schedule settings; times are Toronto wall-clock HH:MM, sorted."""

    timezone: Literal["America/Toronto"] = "America/Toronto"
    times: list[str]
    enabled: bool = Field(description="Admin toggle persisted in the database.")
    server_enabled: bool = Field(
        description="WW_ENABLE_REFRESH=1 and WW_ENABLE_SCHEDULE=1 on this server."
    )
    next_run_at: str | None = Field(
        description="UTC ISO next run; null unless both switches are on."
    )
    min_interval_minutes: int = Field(ge=0)
    max_times: int = Field(ge=1)
    last: list[ScheduleSlotView] = Field(description="Most recent slot decisions.")
    failure: ScheduleFailureView | None = Field(
        description="Scheduled-run failure that turned scheduling off, if any."
    )


class EmptyRequest(Contract):
    """Explicit ``{}`` JSON body for session mutations that carry no fields."""

    model_config = ConfigDict(strict=True)


class ApiTokenIssued(Contract):
    """The plaintext token, returned exactly once; store it now."""

    token: str = Field(
        repr=False,
        description=(
            "Bearer token for /api/agent/v1/. Shown only in this response; "
            "the server keeps a hash."
        ),
    )
    hint: str = Field(description="Prefix and last four characters, e.g. wwf_…ab12.")
    created_at: str = Field(description="UTC ISO 8601.")


class PostingStateRequest(Contract):
    model_config = ConfigDict(strict=True)
    state: PostingState | None = Field(
        description="interested, applied or not_interested; null clears the state."
    )


class PostingViewedRequest(Contract):
    model_config = ConfigDict(strict=True)
    viewed: bool = Field(
        description=("true records the first view time (idempotent); false clears it.")
    )


class PostingMarksResponse(Contract):
    marks: dict[str, PostingMark] = Field(
        description="The caller's marks keyed by posting ID; unmarked IDs are absent."
    )


class AgentPostingSummary(Contract):
    """Compact member-visible posting for agents, with the caller's marks."""

    posting_id: str
    title: str | None
    organization: str | None
    division: JsonValue = None
    location: str | None = Field(description="Stored listing city/location text.")
    openings: JsonValue = Field(default=None, description="Source value as stored.")
    level: JsonValue = None
    application_count: JsonValue = Field(
        default=None, description="Source value as stored."
    )
    deadline: JsonValue = Field(
        default=None,
        description="Source text, Eastern Time, e.g. 'Oct 15, 2026 11:59 PM'.",
    )
    work_arrangement: Literal["Remote", "Hybrid", "In-person"] | None
    country_code: str | None = Field(
        description="Accepted inferred ISO country code; never derived from city."
    )
    country_name: str | None
    targeted_disciplines: list[str]
    compensation: CompensationSort | None = Field(
        description="Source-grounded pay summary; null when none is available."
    )
    first_seen_at: str
    last_seen_at: str
    state: PostingState | None = Field(description="The caller's state, if any.")
    viewed_at: str | None = Field(description="The caller's explicit view time.")


class AgentPostingPage(Contract):
    page: int
    page_size: int
    total: int = Field(description="Matching postings across all pages.")
    total_pages: int
    postings: list[AgentPostingSummary]


class AgentPostingDetail(AgentPostingSummary):
    """Everything a member sees on the web job page, read-only."""

    content: StoredPostingContent | None = Field(
        description="Saved plain-text detail fields; null before detail collection."
    )
    content_observed_at: str | None
    analysis: PostingAnalysisResponse


class AgentRefreshStatus(Contract):
    """Read-only refresh facts; nothing here starts or schedules a refresh."""

    running: bool
    status: Literal["idle", "running", "completed", "failed", "interrupted"]
    last_refreshed_at: str | None = Field(
        description=(
            "UTC ISO time the saved listings were last observed (the web "
            "header's 'Refreshed … ago'); null before the first refresh."
        )
    )
    last_run_finished_at: str | None = Field(
        description="UTC ISO finish time of the most recent run of any outcome."
    )
    next_scheduled_at: str | None = Field(
        description=(
            "UTC ISO time of the next scheduled refresh; null when scheduling "
            "is off, has no times or is inactive on this server."
        )
    )


class AgentIdentityResponse(Contract):
    uid: str
    role: Literal["admin", "member"]
    profile: AccountProfile | None = Field(
        description="Display name/email/photo as shown in the app; null if unavailable."
    )


class DiscordWebhookCreate(Contract):
    """A new alert destination; the URL is encrypted and never returned."""

    model_config = ConfigDict(strict=True)
    label: str = Field(max_length=200, description="Your name for it, 1-60 chars.")
    url: SecretStr = Field(
        max_length=2000,
        repr=False,
        description=(
            "Discord webhook URL: https://discord.com/api/webhooks/<id>/<token> "
            "(discordapp.com, ptb. and canary. hosts are normalised)."
        ),
    )


class DiscordWebhookUpdate(Contract):
    model_config = ConfigDict(strict=True)
    label: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None

    @model_validator(mode="after")
    def some_change(self) -> "DiscordWebhookUpdate":
        if self.label is None and self.enabled is None:
            raise ValueError("Nothing to update.")
        return self


class DiscordWebhookList(Contract):
    webhooks: list[DiscordWebhook]
    limit: int = Field(description="Most webhooks one account may add.")
    available: bool = Field(
        description="False when the backend encryption key is not configured, "
        "so webhooks cannot be added or delivered."
    )
