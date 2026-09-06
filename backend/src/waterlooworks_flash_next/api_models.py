"""Public local API contracts. No authentication secrets or raw source responses."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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
    """An explicit empty JSON object starts one guarded listings-only refresh."""


class ListingRevision(Contract):
    revision_id: int
    content_hash: str
    hash_version: str
    observed_at: str
    run_id: str
    snapshot: StoredListing


class PostingResponse(Contract):
    posting_id: str
    listing: StoredListing
    first_seen_at: str
    last_seen_at: str
    location: str | None
    country: None = Field(
        default=None, description="Unverified; never inferred from city."
    )
    listing_revisions: list[ListingRevision]


class WorkspaceResponse(Contract):
    status: Literal["ready"] = "ready"
    postings: list[PostingResponse]
    manual_refresh_available: bool
    details_available: Literal[False] = False


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
