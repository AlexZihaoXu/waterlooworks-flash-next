"""Read-only compensation ranking, sharing detail-reader authority checks."""

import sqlite3
from decimal import Decimal, localcontext

from waterlooworks_flash_next.analysis_read import read_analysis
from waterlooworks_flash_next.api_models import CompensationSort
from waterlooworks_flash_next.source_enrichment import read_source_enrichment


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


def read_compensation_sorts(
    connection: sqlite3.Connection,
) -> dict[str, CompensationSort]:
    result: dict[str, CompensationSort] = {}
    # Load saved extraction evidence once, not once per posting.
    source = read_source_enrichment(connection)
    ids = connection.execute(
        "SELECT DISTINCT a.posting_id FROM analysis_projections a "
        "JOIN normalized_compensation_rates r USING(projection_id) "
        "JOIN postings p USING(posting_id)"
    ).fetchall()
    candidate_ids = {row[0] for row in ids} | {
        id_ for id_, entry in source.items() if entry.pay
    }
    for posting_id in sorted(candidate_ids):
        analysis = read_analysis(connection, posting_id, source)
        rates = analysis.compensation
        if analysis.compensation_needs_review:
            result[posting_id] = CompensationSort(
                needs_review=True,
                source_rates=rates,
                currency=None,
                period=None,
                minimum=None,
                maximum=None,
                average=None,
            )
            continue
        units = {
            (
                rate.currency,
                "hour" if rate.period in ("week", "month", "year") else rate.period,
            )
            for rate in rates
        }
        if len(units) != 1:
            if rates:
                result[posting_id] = CompensationSort(
                    currency=None,
                    period=None,
                    minimum=None,
                    maximum=None,
                    average=None,
                    source_rates=rates,
                )
            continue
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
        result[posting_id] = CompensationSort(
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
                {
                    rate.hours_per_week
                    for rate in rates
                    if rate.hours_per_week is not None
                },
                key=Decimal,
            ),
        )
    return result
