"""Whole-field conservative compensation fastpaths; no inference or conversions."""

import re
from decimal import Decimal, InvalidOperation, localcontext
from typing import Literal, Self, cast

from pydantic import JsonValue, model_validator

from waterlooworks_flash_next.parser_models import (
    PARSER_VERSION,
    ParserModel,
    ParseState,
    SourceEvidence,
    SourceSpan,
    source_text,
)

Currency = Literal["CAD", "USD", "EUR", "GBP", "AUD", "CHF", "JPY"]
Period = Literal["hour", "day", "week", "month", "year"]
_CURRENCY = (
    r"(?:CAD|USD|EUR|GBP|AUD|CHF|JPY|Canadian dollars?|US "
    r"dollars?|U\.S\. dollars?|euros?|British pounds?|"
    r"Australian dollars?|Swiss francs?|Japanese yen)"
)
_NUMBER = r"(?:[0-9]+|[1-9][0-9]{0,2}(?:,[0-9]{3})+)(?:\.[0-9]{1,2})?[kK]?"
_PERIOD = (
    r"(?:hourly|daily|weekly|monthly|annually|hour|hr|h|day|"
    r"week|wk|month|mo|year|yr|annum)"
)
_PAY = re.compile(
    r"(?:(?:base\s+)?(?:salary|pay|compensation|wage|"
    r"rate)(?:\s+range)?\s*(?::|is|of)?\s*)?"
    r"(?P<bound>at least|more than|over|up to|less than|"
    r"under|starting at|from|>=|<=|>|<)?\s*"
    rf"(?P<c1>{_CURRENCY})?\s*\$?\s*(?P<n1>{_NUMBER})"
    rf"(?:\s*(?:-|–|—|to)\s*(?P<c2>{_CURRENCY})?\s*\$?\s*(?P<n2>{_NUMBER}))?"
    rf"\s*(?P<c3>{_CURRENCY})?"
    rf"(?:\s*(?:/|per\s+|an?\s+)?(?P<period>{_PERIOD}))?"
    r"(?:\s*(?:[,;]\s*|\s+)(?:for\s+)?(?P<hours>[0-9]+(?:\.[0-9]{1,2})?)"
    r"\s*(?:hours?|hrs?)\s*(?:/|per\s+)week)?"
    r"(?:\s*[,;]?\s*paid\s+(?P<payout>biweekly|weekly|monthly|semi-monthly))?\.?",
    re.I,
)
_CURRENCIES: dict[str, Currency] = {
    "cad": "CAD",
    "canadian dollar": "CAD",
    "canadian dollars": "CAD",
    "usd": "USD",
    "us dollar": "USD",
    "us dollars": "USD",
    "u.s. dollar": "USD",
    "u.s. dollars": "USD",
    "eur": "EUR",
    "euro": "EUR",
    "euros": "EUR",
    "gbp": "GBP",
    "british pound": "GBP",
    "british pounds": "GBP",
    "aud": "AUD",
    "australian dollar": "AUD",
    "australian dollars": "AUD",
    "chf": "CHF",
    "swiss franc": "CHF",
    "swiss francs": "CHF",
    "jpy": "JPY",
    "japanese yen": "JPY",
}
_PERIODS: dict[str, Period] = {
    alias: cast(Period, period)
    for period, aliases in (
        ("hour", ("hour", "hourly", "hr", "h")),
        ("day", ("day", "daily")),
        ("week", ("week", "weekly", "wk")),
        ("month", ("month", "monthly", "mo")),
        ("year", ("year", "annually", "yr", "annum")),
    )
    for alias in aliases
}


class PayAmount(ParserModel):
    bound_type: Literal["fixed", "range", "minimum", "maximum"]
    minimum: Decimal | None
    maximum: Decimal | None
    minimum_inclusive: bool | None
    maximum_inclusive: bool | None
    currency: Currency | None = None
    period: Period | None = None
    hours_per_week: Decimal | None = None
    payout_frequency: (
        Literal["weekly", "biweekly", "semi-monthly", "monthly"] | None
    ) = None

    @model_validator(mode="after")
    def valid_bounds(self) -> Self:
        for amount in (self.minimum, self.maximum, self.hours_per_week):
            if amount is not None and (not amount.is_finite() or amount < 0):
                raise ValueError("Pay values must be finite and nonnegative.")
        if self.minimum is None and self.maximum is None:
            raise ValueError("A pay bound is required.")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("Pay bounds must be ordered.")
        if (self.minimum is None) != (self.minimum_inclusive is None) or (
            self.maximum is None
        ) != (self.maximum_inclusive is None):
            raise ValueError("Bound inclusivity must match known bounds.")
        if self.bound_type == "fixed" and (
            self.minimum != self.maximum
            or not self.minimum_inclusive
            or not self.maximum_inclusive
        ):
            raise ValueError("Fixed pay requires equal inclusive bounds.")
        if self.bound_type == "range" and (
            self.minimum is None or self.maximum is None
        ):
            raise ValueError("Range requires two bounds.")
        if self.bound_type == "minimum" and (
            self.minimum is None or self.maximum is not None
        ):
            raise ValueError("Minimum pay requires only a lower bound.")
        if self.bound_type == "maximum" and (
            self.maximum is None or self.minimum is not None
        ):
            raise ValueError("Maximum pay requires only an upper bound.")
        if self.hours_per_week is not None and not 0 < self.hours_per_week <= 168:
            raise ValueError("Weekly hours are outside supported limits.")
        return self


class CompensationResult(ParserModel):
    parser_version: str = PARSER_VERSION
    state: ParseState
    reason: Literal[
        "clear_pay_expression",
        "missing_or_no_amount",
        "complex_or_unsupported",
        "invalid_amount_or_bounds",
    ]
    value: PayAmount | None = None
    evidence: SourceEvidence
    amount_span: SourceSpan | None = None


def _decimal(value: str) -> Decimal:
    if len(value) > 24:
        raise ValueError("Amount size is unsupported.")
    with localcontext() as context:
        context.prec = 50
        return Decimal(value.rstrip("kK").replace(",", "")) * (
            1000 if value[-1:].lower() == "k" else 1
        )


def parse_compensation(
    value: JsonValue, *, source_hash: str | None = None, present: bool = True
) -> CompensationResult:
    field = "Compensation and Benefits:"
    evidence = SourceEvidence(
        field=field, raw=value, present=present, source_hash=source_hash
    )
    text = source_text(value)
    if text is None or not re.search(r"[0-9]", text):
        return CompensationResult(
            state="unknown", reason="missing_or_no_amount", evidence=evidence
        )
    match = _PAY.fullmatch(text) if len(text) <= 1024 else None
    if match is None:
        return CompensationResult(
            state="needs_analysis", reason="complex_or_unsupported", evidence=evidence
        )
    try:
        currencies = {
            _CURRENCIES[match[key].lower()] for key in ("c1", "c2", "c3") if match[key]
        }
        if len(currencies) > 1 or match["bound"] and match["n2"]:
            raise ValueError
        lower = _decimal(match["n1"])
        upper = _decimal(match["n2"]) if match["n2"] else lower
        bound_type: Literal["fixed", "range", "minimum", "maximum"] = (
            "range" if match["n2"] else "fixed"
        )
        minimum: Decimal | None = lower
        maximum: Decimal | None = upper
        min_inclusive: bool | None = True
        max_inclusive: bool | None = True
        bound = (match["bound"] or "").lower()
        if bound in ("at least", "starting at", "from", ">=", "more than", "over", ">"):
            bound_type, maximum, max_inclusive = "minimum", None, None
            min_inclusive = bound in ("at least", "starting at", "from", ">=")
        elif bound:
            bound_type, minimum, min_inclusive = "maximum", None, None
            max_inclusive = bound in ("up to", "<=")
        amount = PayAmount(
            bound_type=bound_type,
            minimum=minimum,
            maximum=maximum,
            minimum_inclusive=min_inclusive,
            maximum_inclusive=max_inclusive,
            currency=next(iter(currencies)) if currencies else None,
            period=_PERIODS[match["period"].lower()] if match["period"] else None,
            hours_per_week=_decimal(match["hours"]) if match["hours"] else None,
            payout_frequency=cast(
                Literal["weekly", "biweekly", "semi-monthly", "monthly"] | None,
                match["payout"].lower() if match["payout"] else None,
            ),
        )
        assert isinstance(value, str)
        start = len(value) - len(value.lstrip())
        return CompensationResult(
            state="recognized",
            reason="clear_pay_expression",
            value=amount,
            evidence=evidence,
            amount_span=SourceSpan(
                field=field,
                start=start,
                end=start + len(text),
                text=text,
                source_hash=source_hash,
            ),
        )
    except (ValueError, InvalidOperation):
        return CompensationResult(
            state="needs_analysis", reason="invalid_amount_or_bounds", evidence=evidence
        )


def validate_pay_evidence(
    candidate: PayAmount, source: str, evidence: SourceSpan
) -> None:
    """Fail closed for future model candidates; cannot promote unsupported semantics.

    Whole-field validation prevents quoting a stipend/equivalent out of complex
    prose as base pay. Complex tiers need a separately reviewed later validator.
    """
    validated = PayAmount.model_validate(candidate.model_dump())
    result = parse_compensation(source)
    if (
        evidence.field != "Compensation and Benefits:"
        or not evidence.matches(source)
        or result.value != validated
        or result.amount_span is None
        or evidence.start != result.amount_span.start
        or evidence.end != result.amount_span.end
    ):
        raise ValueError(
            "Pay candidate is not supported by deterministic source evidence."
        )
