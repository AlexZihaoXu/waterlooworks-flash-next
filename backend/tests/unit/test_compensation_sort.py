"""Primitive rate arithmetic and empty-database reader contracts; no postings."""

import sqlite3
from decimal import Decimal
from pathlib import Path

from waterlooworks_flash_next.api_models import CompensationRate
from waterlooworks_flash_next.compensation_sort import (
    compensation_bounds,
    compensation_sort_for,
    hourly_amount,
    read_compensation_sorts,
)
from waterlooworks_flash_next.database import open_database


def test_hourly_conversion_uses_stated_hours_else_40() -> None:
    assert hourly_amount("800", "week", None) == "20"
    assert hourly_amount("800", "week", "32") == "25"
    assert hourly_amount("52000", "year", None) == "25"
    assert hourly_amount("5200", "month", "30") == "40"
    assert hourly_amount("25.50", "hour", None) == "25.50"
    assert hourly_amount("200", "day", "40") == "200"
    assert hourly_amount(None, "month", None) is None


def test_fixed_and_range_midpoints_are_exact() -> None:
    assert compensation_bounds([("25.00", "30.01")]) == (
        Decimal("25.00"),
        Decimal("30.01"),
        Decimal("27.505"),
    )
    assert compensation_bounds([("25", "25")]) == (Decimal("25"),) * 3


def test_tiers_use_outer_bounds_not_weighted_averages() -> None:
    assert compensation_bounds([("10", "20"), ("30", "40")]) == (
        Decimal("10"),
        Decimal("40"),
        Decimal("25"),
    )


def test_open_bounds_do_not_invent_missing_endpoints() -> None:
    assert compensation_bounds([("25", None)]) == (Decimal("25"), None, None)
    assert compensation_bounds([(None, "30")]) == (None, Decimal("30"), None)
    assert compensation_bounds([("10", "20"), ("30", None)]) == (
        Decimal("10"),
        None,
        None,
    )
    assert compensation_bounds([]) == (None, None, None)


def _rate(**update: object) -> CompensationRate:
    # Primitive numeric rates with empty source fields, not posting fixtures.
    base = CompensationRate(
        bound_type="range",
        minimum="20",
        maximum="30",
        minimum_inclusive=True,
        maximum_inclusive=True,
        currency="CAD",
        period="hour",
        hours_per_week=None,
        payout_frequency=None,
        eligibility=None,
        source_field="",
        evidence_text="",
    )
    return base.model_copy(update=update)


def test_sort_summary_is_derived_from_merged_rates_alone() -> None:
    assert compensation_sort_for([], needs_review=False) is None
    review = compensation_sort_for([_rate()], needs_review=True)
    assert review is not None and review.needs_review
    assert review.currency is None and review.average is None
    single = compensation_sort_for([_rate()], needs_review=False)
    assert single is not None
    assert (single.currency, single.period) == ("CAD", "hour")
    assert (single.minimum, single.maximum, single.average) == ("20", "30", "25")
    assert not single.estimated
    weekly = compensation_sort_for(
        [_rate(bound_type="fixed", minimum="800", maximum="800", period="week")],
        needs_review=False,
    )
    assert weekly is not None and weekly.period == "hour"
    assert weekly.average == "20" and weekly.estimated and weekly.assumed_hours
    mixed = compensation_sort_for([_rate(), _rate(currency="USD")], needs_review=False)
    assert mixed is not None and mixed.currency is None and mixed.source_rates


def test_bulk_reader_uses_fixed_query_count_on_empty_database(
    tmp_path: Path,
) -> None:
    connection = open_database(tmp_path / "empty.sqlite3")
    connection.row_factory = sqlite3.Row
    statements: list[str] = []
    connection.set_trace_callback(statements.append)
    try:
        assert read_compensation_sorts(connection) == {}
    finally:
        connection.set_trace_callback(None)
        connection.close()
    # Bulk reads only: no per-posting analysis lookups such as field evidence.
    assert not any("normalized_field_evidence" in sql for sql in statements)
    assert len(statements) <= 12
