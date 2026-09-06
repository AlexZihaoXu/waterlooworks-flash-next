"""Verified detail metadata and explicitly unimplemented content fields.

These are storage contracts, not a parser for raw metadata or untrusted HTML.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DetailMetadata(BaseModel):
    """Friendly names for observed metadata: position/org/div/divId and statuses."""

    model_config = ConfigDict(
        extra="forbid", strict=True, hide_input_in_errors=True, frozen=True
    )
    title: str = Field(repr=False)
    organization: str = Field(repr=False)
    division: str = Field(repr=False)
    division_id: int
    status: str
    internal_status: str
    has_prescreening: bool


class DetailContent(BaseModel):
    """Reserved fields: only null is accepted until extraction is verified."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    work_term: None = None
    duration: None = None
    description: None = None
    responsibilities: None = None
    qualifications: None = None
    compensation_text: None = None
    work_arrangement: None = None
    location_details: None = None
    application_instructions: None = None


class StoredDetail(BaseModel):
    """One posting's metadata-only snapshot; no raw HTML or application state."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    id: str = Field(pattern=r"^[0-9]+$")
    metadata: DetailMetadata = Field(repr=False)
    content: DetailContent = Field(default_factory=DetailContent, repr=False)
    coverage: Literal["metadata_only"] = "metadata_only"
