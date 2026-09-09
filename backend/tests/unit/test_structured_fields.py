"""Generic field/value primitives only; no fabricated postings or API responses."""

from datetime import UTC, datetime

import pytest
from pydantic import JsonValue

from waterlooworks_flash_next.structured_fields import (
    parse_count,
    parse_deadline,
    parse_fields,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, 0),
        (12, 12),
        (12.0, 12),
        ("1,234", 1234),
        (None, None),
        (True, None),
        (-1, None),
        (float("inf"), None),
        (float("nan"), None),
        ("12,34", None),
        ("1.0", None),
        ("-1", None),
        (9007199254740992, None),
    ],
)
def test_count_projection_never_defaults_unknown_to_zero(
    value: JsonValue, expected: int | None
) -> None:
    assert parse_count(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "Nov 1, 2026 1:30 AM",
        "Mar 8, 2026 2:30 AM",
        "Feb 30, 2026 1:00 PM",
        "Jan 1, 2026 0:30 AM",
        "2026-01-01",
        None,
    ],
)
def test_deadline_rejects_ambiguous_nonexistent_and_invalid(value: JsonValue) -> None:
    assert parse_deadline(value) is None


def test_deadline_uses_toronto_not_host_timezone() -> None:
    assert parse_deadline("Jan 1, 2026 1:05 PM") == datetime(
        2026, 1, 1, 18, 5, tzinfo=UTC
    )
    assert parse_deadline("Jul 1, 2026 1:05 PM") == datetime(
        2026, 7, 1, 17, 5, tzinfo=UTC
    )


def test_exact_labels_multiple_levels_and_missing_null_evidence() -> None:
    parsed = parse_fields(
        {
            "Employment Location Arrangement:": "Hybrid",
            "Level:": "Junior\nSenior\nFuture",
            "Job - Country:": ".",
        },
        {"application_count": None},
    )
    assert parsed.arrangement.value == "Hybrid"
    assert parsed.levels.value == ("Junior", "Senior")
    assert parsed.levels.unknown_values == ("Future",)
    assert parsed.country.value is None
    assert parsed.application_count.evidence.present
    assert not parsed.openings.evidence.present
    assert parsed.application_count.value is None
    assert (
        parse_fields({"Employment Location Arrangement:": "remote"}).arrangement.value
        is None
    )


def test_work_term_and_duration_do_not_infer_month_equivalence() -> None:
    parsed = parse_fields(
        {
            "Work Term:": "2027 - Winter",
            "Work Term Duration:": "2 work-term commitment preferred",
        }
    )
    assert parsed.work_term.value is not None
    assert parsed.work_term.value.year == 2027
    assert parsed.duration.value is not None
    assert parsed.duration.value.count == 2
    assert parsed.duration.value.unit == "work_term"
    assert parsed.duration.value.requirement == "preferred"
    assert (
        parse_fields({"Work Term Duration:": "8 month required"}).duration.value
        is not None
    )
    assert parse_fields({"Work Term Duration:": "four months"}).duration.value is None


@pytest.mark.parametrize(
    "text",
    [
        "4 month work term",
        "8 month consecutive work term preferred",
        "8 month consecutive work term required",
    ],
)
def test_duration_labeled_work_term_suffix(text: str) -> None:
    result = parse_fields({"Work Term Duration:": text}).duration
    assert result.value is not None and result.value.unit == "month"
    assert result.evidence.raw == text
