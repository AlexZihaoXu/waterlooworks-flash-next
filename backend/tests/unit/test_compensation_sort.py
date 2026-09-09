from decimal import Decimal

from waterlooworks_flash_next.compensation_sort import (
    compensation_bounds,
    hourly_amount,
)


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
