"""Exact Standard Luna cost estimates from reported counters, never text length.

Integration must record every actual attempt, even rejected/retried output, and
replace that attempt's record when its final usage arrives rather than append
streaming duplicates. Missing usage is unknown, not zero. No provider I/O here.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

MODEL = "gpt-5.6-luna"
PRICING_DATE = "2026-09-09"
LONG_CONTEXT_THRESHOLD = 272_000
INPUT_USD_PER_MILLION = Decimal("0.20")
CACHED_USD_PER_MILLION = Decimal("0.02")
CACHE_WRITE_USD_PER_MILLION = Decimal("0.25")
OUTPUT_USD_PER_MILLION = Decimal("1.20")

Counter = Annotated[int, Field(strict=True, ge=0, le=2**53 - 1)]


class UsageCounters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    input_tokens: Counter | None = None
    cached_tokens: Counter | None = None
    cache_write_tokens: Counter | None = None
    output_tokens: Counter | None = None
    reasoning_tokens: Counter | None = None

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.input_tokens is not None and (
            (self.cached_tokens or 0) + (self.cache_write_tokens or 0)
            > self.input_tokens
        ):
            raise ValueError("Input subsets exceed reported input.")
        if (
            self.output_tokens is not None
            and self.reasoning_tokens is not None
            and self.reasoning_tokens > self.output_tokens
        ):
            raise ValueError("Reasoning exceeds reported output.")
        return self

    @property
    def ordinary_input_tokens(self) -> int | None:
        if (
            self.input_tokens is None
            or self.cached_tokens is None
            or self.cache_write_tokens is None
        ):
            return None
        return self.input_tokens - self.cached_tokens - self.cache_write_tokens

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None or self.output_tokens is None:
            return None
        # Cached reads/writes are input subsets; reasoning is an output subset.
        return self.input_tokens + self.output_tokens


def price_usage(
    usage: UsageCounters, *, model: str = MODEL, service_tier: str = "default"
) -> Decimal | None:
    """USD estimate; not an invoice, and not valid for Batch/Flex/other models."""
    ordinary = usage.ordinary_input_tokens
    if (
        model != MODEL
        or service_tier != "default"
        or ordinary is None
        or usage.cached_tokens is None
        or usage.cache_write_tokens is None
        or usage.output_tokens is None
    ):
        return None
    # Above the threshold, multipliers apply to the FULL request, not its excess.
    is_long = (
        usage.input_tokens is not None and usage.input_tokens > LONG_CONTEXT_THRESHOLD
    )
    input_multiplier = Decimal(2) if is_long else Decimal(1)
    output_multiplier = Decimal("1.5") if is_long else Decimal(1)
    with localcontext() as context:
        context.prec = 40
        return (
            (
                ordinary * INPUT_USD_PER_MILLION
                + usage.cached_tokens * CACHED_USD_PER_MILLION
                + usage.cache_write_tokens * CACHE_WRITE_USD_PER_MILLION
            )
            * input_multiplier
            + usage.output_tokens * OUTPUT_USD_PER_MILLION * output_multiplier
        ) / Decimal(1_000_000)


@dataclass(frozen=True)
class CostSummary:
    attempts: int
    unpriced_attempts: int
    known_usd: Decimal

    @property
    def total_usd(self) -> Decimal | None:
        return None if self.unpriced_attempts else self.known_usd


def summarize_costs(attempts: Mapping[str, Decimal | None]) -> CostSummary:
    """Each key is a distinct actual attempt; unknown entries make total unknown."""
    known = Decimal(0)
    unpriced = 0
    with localcontext() as context:
        context.prec = 50
        for value in attempts.values():
            if value is None:
                unpriced += 1
            elif not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError("Invalid usage cost.")
            else:
                known += value
    return CostSummary(len(attempts), unpriced, known)
