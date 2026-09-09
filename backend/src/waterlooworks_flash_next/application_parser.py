"""Saved application instructions only; never navigate, fetch or apply."""

import re
from collections.abc import Iterator, Mapping
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, JsonValue

from waterlooworks_flash_next.parser_models import (
    MAX_FIELD_LENGTH,
    PARSER_VERSION,
    ParserModel,
    ParseState,
    SourceEvidence,
    SourceSpan,
)

APPLICATION_FIELDS = (
    "Additional Application Information:",
    "Additional Information:",
    "Application Method:",
)
_URL = re.compile(r"https?://[^\s<>\"']+", re.I)
_EXTERNAL = re.compile(
    (
        r"\b(?:external(?:ly)?|directly (?:to|at|through|with)|must "
        r"also apply|may also apply|both applications|apply\b.{0,40}\b"
        r"(?:employer|company) (?:website|site|portal))\b"
    ),
    re.I,
)
_REQUIRED = re.compile(
    (
        r"\b(?:must\s+(?:also\s+)?apply|(?:both|external) "
        r"applications? (?:is |are )?(?:required|mandatory)|"
        r"required to apply|apply\b.{0,80}\bboth\b|"
        r"apply (?:through|via|on) WaterlooWorks (?:and|&) directly)"
    ),
    re.I,
)
_NEGATED = re.compile(
    (
        r"\b(?:no external application(?:s)? (?:is |are "
        r")?(?:required|needed|necessary)|external "
        r"application(?:s)? (?:is |are )?not (?:required|needed|"
        r"necessary)|do not apply externally)\b"
    ),
    re.I,
)
_OPTIONAL = re.compile(
    (
        r"\b(?:may optionally apply|may (?:also )?apply|"
        r"optional(?:ly)?\b.{0,50}\b(?:apply|application)|"
        r"(?:apply|application)\b.{0,50}\boptional)\b"
    ),
    re.I,
)
_INFORMATIONAL = re.compile(
    (
        r"\b(?:info(?:rmation)?[- ]session|company website|learn "
        r"more|social media|webinar)\b"
    ),
    re.I,
)


class ApplicationLink(ParserModel):
    url: str = Field(repr=False)
    classification: Literal[
        "required", "optional", "not_required", "uncertain", "informational"
    ]
    evidence: SourceSpan
    instruction: SourceSpan


class ApplicationResult(ParserModel):
    parser_version: str = PARSER_VERSION
    state: ParseState
    external_application_required: bool | None
    application_route: Literal["waterlooworks_and_employer", "employer", "unknown"]
    application_method: SourceEvidence
    instruction_evidence: tuple[SourceSpan, ...]
    external_application_links: tuple[ApplicationLink, ...]
    source_fields: dict[str, str] = Field(repr=False)
    unscanned_fields: tuple[str, ...]


def _safe_url(raw: str) -> str | None:
    # Strip prose punctuation, not meaningful query strings or balanced parentheses.
    value = raw.rstrip(".,;")
    while value.endswith(")") and value.count(")") > value.count("("):
        value = value[:-1]
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme.lower() not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or "\\" in value
            or any(
                ord(char) < 32 or ord(char) == 127 or char.isspace() for char in value
            )
        ):
            return None
        _ = parsed.port
        return value
    except ValueError:
        return None


def _instruction_clauses(value: str) -> Iterator[tuple[int, int]]:
    urls = [
        (match.start(), match.start() + len(url))
        for match in _URL.finditer(value)
        if (url := _safe_url(match[0]))
    ]
    index = 0
    start = 0
    for separator in re.finditer(r"[;\n]|\.(?=\s|$)", value):
        while index < len(urls) and urls[index][1] <= separator.start():
            index += 1
        if index < len(urls) and urls[index][0] <= separator.start() < urls[index][1]:
            continue
        if value[start : separator.end()].strip():
            yield start, separator.end()
        start = separator.end()
    if value[start:].strip():
        yield start, len(value)


def parse_application_instructions(
    fields: Mapping[str, JsonValue], *, source_hash: str | None = None
) -> ApplicationResult:
    links: list[ApplicationLink] = []
    instructions: list[SourceSpan] = []
    sources: dict[str, str] = {}
    unscanned: list[str] = []
    signals: set[bool] = set()
    ww_required = False
    method = SourceEvidence(
        field="Application Method:",
        raw=fields.get("Application Method:"),
        present="Application Method:" in fields,
        source_hash=source_hash,
    )
    for field in APPLICATION_FIELDS:
        value = fields.get(field)
        if not isinstance(value, str):
            continue
        sources[field] = value
        if len(value) > MAX_FIELD_LENGTH:
            unscanned.append(field)
            continue
        # Separators inside safe URLs are not instruction boundaries.
        for start, end in _instruction_clauses(value):
            clause = value[start:end]
            span = SourceSpan(
                field=field,
                start=start,
                end=end,
                text=clause,
                source_hash=source_hash,
            )
            external = bool(_EXTERNAL.search(clause))
            negative, optional = (
                bool(_NEGATED.search(clause)),
                bool(_OPTIONAL.search(clause)),
            )
            positive = external and bool(_REQUIRED.search(_NEGATED.sub("", clause)))
            # Unknown negation/conditional wording is not a confident positive.
            uncertain = bool(re.search(r"\b(?:if|unless)\b", clause, re.I)) or (
                bool(re.search(r"\b(?:not|may|can)\b", clause, re.I))
                and not (negative or optional)
            )
            if not uncertain and (negative or optional and external or positive):
                instructions.append(span)
                if negative or optional and external:
                    signals.add(False)
                if positive:
                    signals.add(True)
                    ww_required |= bool(re.search(r"\bWaterlooWorks\b", clause, re.I))
            for match in _URL.finditer(clause):
                url = _safe_url(match[0])
                if url is None:
                    continue
                classification: Literal[
                    "required",
                    "optional",
                    "not_required",
                    "uncertain",
                    "informational",
                ] = "uncertain"
                if uncertain or positive and (negative or optional):
                    classification = "uncertain"
                elif negative:
                    classification = "not_required"
                elif optional:
                    classification = "optional"
                elif positive:
                    classification = "required"
                elif _INFORMATIONAL.search(clause):
                    classification = "informational"
                begin = start + match.start()
                links.append(
                    ApplicationLink(
                        url=url,
                        classification=classification,
                        evidence=SourceSpan(
                            field=field,
                            start=begin,
                            end=begin + len(url),
                            text=url,
                            source_hash=source_hash,
                        ),
                        instruction=span,
                    )
                )
    required = next(iter(signals)) if len(signals) == 1 and not unscanned else None
    route: Literal["waterlooworks_and_employer", "employer", "unknown"] = "unknown"
    if required is True:
        route = (
            "waterlooworks_and_employer"
            if ww_required or method.raw == "WaterlooWorks"
            else "employer"
        )
    return ApplicationResult(
        state="recognized"
        if required is not None
        else "needs_analysis"
        if len(signals) > 1 or unscanned
        else "unknown",
        external_application_required=required,
        application_route=route,
        application_method=method,
        instruction_evidence=tuple(instructions),
        external_application_links=tuple(links),
        source_fields=sources,
        unscanned_fields=tuple(unscanned),
    )
