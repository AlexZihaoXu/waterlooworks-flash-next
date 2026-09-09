"""Pure parser contracts; caller-supplied source identity, no storage or actions."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

PARSER_VERSION = "structured-fields-v1"
MAX_FIELD_LENGTH = 200_000
ParseState = Literal["recognized", "unknown", "needs_analysis"]
Requirement = Literal["required", "preferred", "unspecified"]


class ParserModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, hide_input_in_errors=True
    )


class SourceEvidence(ParserModel):
    source: Literal["content", "listing"] = "content"
    field: str
    present: bool
    raw: JsonValue = Field(repr=False)
    source_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class SourceSpan(ParserModel):
    field: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    text: str = Field(repr=False)
    source_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    def matches(self, source: str) -> bool:
        return (
            self.start < self.end <= len(source)
            and source[self.start : self.end] == self.text
        )


class ParsedField[T](ParserModel):
    parser_version: str = PARSER_VERSION
    state: ParseState
    value: T | None
    evidence: SourceEvidence
    unknown_values: tuple[str, ...] = ()


def source_text(value: JsonValue) -> str | None:
    if not isinstance(value, str) or len(value) > MAX_FIELD_LENGTH or not value.strip():
        return None
    return value.strip()
