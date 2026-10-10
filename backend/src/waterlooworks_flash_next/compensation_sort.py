"""Read-only compensation ranking, sharing detail-reader authority checks."""

import sqlite3
from decimal import Decimal, localcontext

from waterlooworks_flash_next.analysis_read import read_compensations
from waterlooworks_flash_next.api_models import CompensationRate, CompensationSort


def hourly_amount(
    value: str | None, period: str | None, hours: str | None
) -> str | None:
    """Derived display/ranking only; never rewrite source compensation."""
    if value is None or period not in ("week", "month", "year"):
        return value
    with localcontext() as context:
        context.prec = max(38, len(value) * 2 + len(hours or "40") + 4)
        weekly_hours = Decimal(hours) if hours is not None else Decimal(40)
        if weekly_hours <= 0:
            raise ValueError("Working hours must be positive.")
        amount = Decimal(value)
        if period == "month":
            amount = amount * 12 / 52
        elif period == "year":
            amount = amount / 52
        return format(amount / weekly_hours, "f")


def compensation_bounds(
    bounds: list[tuple[str | None, str | None]],
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    if not bounds:
        return None, None, None
    lower = [Decimal(lo) for lo, _ in bounds if lo is not None]
    upper = [Decimal(hi) for _, hi in bounds if hi is not None]
    minimum = min(lower) if len(lower) == len(bounds) else None
    maximum = max(upper) if len(upper) == len(bounds) else None
    with localcontext() as context:
        context.prec = (
            max(28, max(len(str(v)) for v in lower + upper) * 2 + 4)
            if lower or upper
            else 28
        )
        average = (
            (minimum + maximum) / 2
            if minimum is not None and maximum is not None
            else None
        )
    return minimum, maximum, average


def compensation_sort_for(
    rates: list[CompensationRate], *, needs_review: bool
) -> CompensationSort | None:
    """Ranking summary of already merged, source-anchored rates; None when unpaid."""
    if needs_review:
        return CompensationSort(
            needs_review=True,
            source_rates=rates,
            currency=None,
            period=None,
            minimum=None,
            maximum=None,
            average=None,
        )
    units = {
        (
            rate.currency,
            "hour" if rate.period in ("week", "month", "year") else rate.period,
        )
        for rate in rates
    }
    if len(units) != 1:
        if not rates:
            return None
        return CompensationSort(
            currency=None,
            period=None,
            minimum=None,
            maximum=None,
            average=None,
            source_rates=rates,
        )
    currency, period = next(iter(units))
    bounds = [
        (
            hourly_amount(rate.minimum, rate.period, rate.hours_per_week)
            if rate.bound_type != "maximum"
            else None,
            hourly_amount(
                rate.minimum if rate.bound_type == "fixed" else rate.maximum,
                rate.period,
                rate.hours_per_week,
            )
            if rate.bound_type != "minimum"
            else None,
        )
        for rate in rates
    ]
    minimum, maximum, average = compensation_bounds(bounds)
    return CompensationSort(
        source_rates=rates,
        currency=currency,
        period=period,
        minimum=format(minimum, "f") if minimum is not None else None,
        maximum=format(maximum, "f") if maximum is not None else None,
        average=format(average, "f") if average is not None else None,
        estimated=any(rate.period in ("week", "month", "year") for rate in rates),
        assumed_hours=any(
            rate.hours_per_week is None and rate.period in ("week", "month", "year")
            for rate in rates
        ),
        annualized=any(rate.period in ("month", "year") for rate in rates),
        hours_per_week=sorted(
            {rate.hours_per_week for rate in rates if rate.hours_per_week is not None},
            key=Decimal,
        ),
    )


def read_compensation_sorts(
    connection: sqlite3.Connection,
) -> dict[str, CompensationSort]:
    """Bulk read for the workspace: fixed query count, no per-posting analysis."""
    result: dict[str, CompensationSort] = {}
    for posting_id, (rates, needs_review) in read_compensations(connection).items():
        summary = compensation_sort_for(rates, needs_review=needs_review)
        if summary is not None:
            result[posting_id] = summary
    return result
