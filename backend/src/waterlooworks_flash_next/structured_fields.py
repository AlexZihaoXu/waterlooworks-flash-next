"""Deterministic saved-field projections; geography is not work arrangement."""

import math
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal, cast
from zoneinfo import ZoneInfo

from pydantic import JsonValue

from waterlooworks_flash_next.parser_models import (
    ParsedField,
    ParserModel,
    SourceEvidence,
    source_text,
)
from waterlooworks_flash_next.targeted_disciplines import split_targeted_disciplines

Arrangement = Literal["Remote", "Hybrid", "In-person"]
Level = Literal["Junior", "Intermediate", "Senior"]
MAX_COUNT = 2**53 - 1
_COUNT = re.compile(r"(?:[0-9]+|[1-9][0-9]{0,2}(?:,[0-9]{3})+)")
_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


class WorkTerm(ParserModel):
    year: int
    season: Literal["Winter", "Spring", "Summer", "Fall"]


class Duration(ParserModel):
    count: int
    unit: Literal["month", "work_term"]
    requirement: Literal["required", "preferred", "unspecified"]


class StructuredFields(ParserModel):
    arrangement: ParsedField[Arrangement]
    city: ParsedField[str]
    province: ParsedField[str]
    country: ParsedField[str]
    levels: ParsedField[tuple[Level, ...]]
    work_term: ParsedField[WorkTerm]
    duration: ParsedField[Duration]
    job_type: ParsedField[str]
    targeted_disciplines: ParsedField[tuple[str, ...]]
    application_count: ParsedField[int]
    openings: ParsedField[int]
    deadline: ParsedField[datetime]


def parse_count(value: JsonValue) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= MAX_COUNT else None
    if isinstance(value, float):
        return (
            int(value)
            if math.isfinite(value) and value.is_integer() and 0 <= value <= MAX_COUNT
            else None
        )
    text = source_text(value)
    if text is None or len(text) > 32 or not _COUNT.fullmatch(text):
        return None
    result = int(text.replace(",", ""))
    return result if result <= MAX_COUNT else None


def parse_deadline(value: JsonValue) -> datetime | None:
    """Same grammar/Toronto reject policy as frontend/lib/deadlines.ts."""
    text = source_text(value)
    match = re.fullmatch(
        r"([A-Z][a-z]{2}) ([0-9]{1,2}), ([0-9]{4}) ([0-9]{1,2}):([0-9]{2}) (AM|PM)",
        text or "",
    )
    if match is None or match[1] not in _MONTHS or not 1 <= int(match[4]) <= 12:
        return None
    try:
        wall = datetime(
            int(match[3]),
            _MONTHS.index(match[1]) + 1,
            int(match[2]),
            int(match[4]) % 12 + (12 if match[6] == "PM" else 0),
            int(match[5]),
        )
        zone = ZoneInfo("America/Toronto")
        candidates = {
            instant
            for fold in (0, 1)
            if (instant := wall.replace(tzinfo=zone, fold=fold).astimezone(UTC))
            .astimezone(zone)
            .replace(tzinfo=None)
            == wall
        }
        return next(iter(candidates)) if len(candidates) == 1 else None
    except (ValueError, OverflowError):
        return None


def _result[T](
    evidence: SourceEvidence, value: T | None, unknown: tuple[str, ...] = ()
) -> ParsedField[T]:
    return ParsedField[T](
        state="recognized" if value is not None else "unknown",
        value=value,
        evidence=evidence,
        unknown_values=unknown,
    )


def _geography(value: JsonValue) -> str | None:
    text = source_text(value)
    if text is None or text.casefold() in {
        "unknown",
        "n/a",
        "na",
        "not specified",
        "not available",
        "tbd",
    }:
        return None
    return text if any(char.isalpha() for char in text) else None


def parse_fields(
    fields: Mapping[str, JsonValue],
    listing: Mapping[str, JsonValue] | None = None,
    *,
    content_hash: str | None = None,
    listing_hash: str | None = None,
) -> StructuredFields:
    listing = listing or {}

    def evidence(
        label: str, key: str | None = None, *, prefer_listing: bool = False
    ) -> SourceEvidence:
        if key is not None and (
            label not in fields or prefer_listing and key in listing
        ):
            return SourceEvidence(
                source="listing",
                field=key,
                raw=listing.get(key),
                present=key in listing,
                source_hash=listing_hash,
            )
        return SourceEvidence(
            field=label,
            raw=fields.get(label),
            present=label in fields,
            source_hash=content_hash,
        )

    arrangement = evidence("Employment Location Arrangement:")
    arrangement_text = source_text(arrangement.raw)
    levels = evidence("Level:", "level")
    level_text = source_text(levels.raw)
    lines = tuple(
        dict.fromkeys(
            line.strip() for line in (level_text or "").splitlines() if line.strip()
        )
    )
    known_levels = tuple(
        cast(Level, line)
        for line in lines
        if line in ("Junior", "Intermediate", "Senior")
    )
    unknown_levels = tuple(line for line in lines if line not in known_levels)
    term = evidence("Work Term:")
    term_match = re.fullmatch(
        r"([0-9]{4})\s*-\s*(Winter|Spring|Summer|Fall)", source_text(term.raw) or ""
    )
    work_term = (
        WorkTerm(
            year=int(term_match[1]),
            season=cast(Literal["Winter", "Spring", "Summer", "Fall"], term_match[2]),
        )
        if term_match and int(term_match[1]) > 0
        else None
    )
    duration = evidence("Work Term Duration:")
    duration_match = re.fullmatch(
        (
            r"([1-9][0-9]?)\s+(months?(?: consecutive)?(?: work term)?|"
            r"work[- ]terms?(?: commitment)?)(?:\s+(required|preferred))?"
        ),
        source_text(duration.raw) or "",
    )
    parsed_duration = (
        Duration(
            count=int(duration_match[1]),
            unit="month" if duration_match[2].startswith("month") else "work_term",
            requirement=cast(
                Literal["required", "preferred", "unspecified"],
                duration_match[3] or "unspecified",
            ),
        )
        if duration_match
        else None
    )
    city, province, country = (
        evidence(label)
        for label in ("Job - City:", "Job - Province/State:", "Job - Country:")
    )
    job_type = evidence("Job Type:")
    targeted = evidence("Targeted Degrees and Disciplines:")
    targeted_text = source_text(targeted.raw)
    targeted_values = (
        tuple(split_targeted_disciplines(targeted_text)) if targeted_text else ()
    )
    applications = evidence(
        "Application Count:", "application_count", prefer_listing=True
    )
    openings = evidence("Number of Job Openings:", "openings", prefer_listing=True)
    deadline = evidence("Application Deadline", "deadline", prefer_listing=True)
    return StructuredFields(
        arrangement=_result(
            arrangement,
            cast(Arrangement, arrangement_text)
            if arrangement_text in ("Remote", "Hybrid", "In-person")
            else None,
        ),
        city=_result(city, _geography(city.raw)),
        province=_result(province, _geography(province.raw)),
        country=_result(country, _geography(country.raw)),
        levels=_result(levels, known_levels or None, unknown_levels),
        work_term=_result(term, work_term),
        duration=_result(duration, parsed_duration),
        job_type=_result(job_type, source_text(job_type.raw)),
        targeted_disciplines=_result(targeted, targeted_values or None),
        application_count=_result(applications, parse_count(applications.raw)),
        openings=_result(openings, parse_count(openings.raw)),
        deadline=_result(deadline, parse_deadline(deadline.raw)),
    )
