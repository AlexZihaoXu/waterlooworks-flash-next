"""Conservative pay expressions and evidence primitives, no complete postings."""

from decimal import Decimal

import pytest

from waterlooworks_flash_next.compensation_parser import (
    PayAmount,
    parse_compensation,
    validate_pay_evidence,
)
from waterlooworks_flash_next.parser_models import SourceSpan


@pytest.mark.parametrize(
    ("text", "minimum", "maximum", "currency", "period"),
    [
        ("USD $20 - $25/hour", "20", "25", "USD", "hour"),
        ("$80k", "80000", "80000", None, None),
        ("20 Canadian dollars per hour", "20", "20", "CAD", "hour"),
        ("EUR 2,500/month paid biweekly", "2500", "2500", "EUR", "month"),
    ],
)
def test_clear_amounts_ranges_currency_and_period(
    text: str, minimum: str, maximum: str, currency: str | None, period: str | None
) -> None:
    result = parse_compensation(text)
    assert result.state == "recognized"
    assert result.value is not None
    assert result.value.minimum == Decimal(minimum)
    assert result.value.maximum == Decimal(maximum)
    assert result.value.currency == currency
    assert result.value.period == period
    assert result.evidence.raw == text


def test_bounds_hours_and_payout_are_not_inferred() -> None:
    result = parse_compensation("more than $20/hour, 37.5 hours/week")
    assert result.value is not None
    assert result.value.minimum == Decimal("20")
    assert result.value.maximum is None
    assert not result.value.minimum_inclusive
    assert result.value.hours_per_week == Decimal("37.5")
    monthly = parse_compensation("EUR 2,500/month paid biweekly").value
    assert monthly is not None and monthly.payout_frequency == "biweekly"


@pytest.mark.parametrize(
    "text",
    [
        "$20/hour plus $500 housing",
        "$20 junior; $30 senior",
        "CAD tools required; salary $25/hour",
        "$50,00 per year",
        "$25/hour equivalent to $50k/year",
        "$30-$20/hour",
        "USD 20-CAD 30/hour",
        "$20/hour plus benefits",
    ],
)
def test_ambiguous_tiers_benefits_equivalents_and_bad_numbers_need_analysis(
    text: str,
) -> None:
    result = parse_compensation(text)
    assert result.state == "needs_analysis"
    assert result.value is None


@pytest.mark.parametrize("text", ["", "To be discussed", "Competitive salary", None])
def test_no_amount_generic_text_does_not_request_model(text: str | None) -> None:
    assert parse_compensation(text).state == "unknown"


def test_weekly_hours_without_pay_are_not_split_into_an_amount() -> None:
    source = "35 hours/week"
    result = parse_compensation(source)
    assert result.state == "needs_analysis"
    assert result.value is None

    false_pay = PayAmount(
        bound_type="fixed",
        minimum=Decimal("3"),
        maximum=Decimal("3"),
        minimum_inclusive=True,
        maximum_inclusive=True,
        hours_per_week=Decimal("5"),
    )
    span = SourceSpan(
        field="Compensation and Benefits:",
        start=0,
        end=len(source),
        text=source,
    )
    with pytest.raises(ValueError, match="supported"):
        validate_pay_evidence(false_pay, source, span)


def test_model_evidence_cannot_invent_annual_period_or_currency() -> None:
    text = "$80k"
    result = parse_compensation(text)
    assert result.value is not None
    span = SourceSpan(
        field="Compensation and Benefits:", start=0, end=len(text), text=text
    )
    validate_pay_evidence(result.value, text, span)
    for candidate in [
        result.value.model_copy(update={"period": "year"}),
        result.value.model_copy(update={"currency": "CAD"}),
    ]:
        with pytest.raises(ValueError, match="supported"):
            validate_pay_evidence(candidate, text, span)


def test_model_cannot_quote_a_benefit_or_change_bounds() -> None:
    source = "Housing stipend: $500/month"
    amount = parse_compensation("$500/month").value
    assert amount is not None
    span = SourceSpan(
        field="Compensation and Benefits:", start=17, end=len(source), text="$500/month"
    )
    with pytest.raises(ValueError, match="supported"):
        validate_pay_evidence(amount, source, span)


def test_exclusive_upper_bound_and_decimal_k_expansion() -> None:
    result = parse_compensation("under EUR 2.5k/month")
    assert result.value is not None
    assert result.value.minimum is None
    assert result.value.maximum == Decimal("2500")
    assert result.value.maximum_inclusive is False
