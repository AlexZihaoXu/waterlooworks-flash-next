"""Token-count primitives only; no provider response fixtures or API calls."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from waterlooworks_flash_next.openai_usage import (
    UsageCounters,
    price_usage,
    summarize_costs,
)


def test_cache_categories_partition_input_and_reasoning_is_not_added_twice() -> None:
    counts = UsageCounters(
        input_tokens=30_000,
        cached_tokens=10_000,
        cache_write_tokens=10_000,
        output_tokens=10_000,
        reasoning_tokens=2_500,
    )
    assert counts.ordinary_input_tokens == 10_000
    assert counts.total_tokens == 40_000
    assert price_usage(counts) == Decimal("0.0167")


def test_long_context_threshold_applies_to_entire_request() -> None:
    short = UsageCounters(
        input_tokens=272_000, cached_tokens=0, cache_write_tokens=0, output_tokens=100
    )
    long = UsageCounters(
        input_tokens=272_001, cached_tokens=0, cache_write_tokens=0, output_tokens=100
    )
    assert price_usage(short) == Decimal("0.05452")
    assert price_usage(long) == Decimal("0.1089804")
    cached = UsageCounters(
        input_tokens=272_001,
        cached_tokens=1_000,
        cache_write_tokens=1_000,
        output_tokens=1_000,
    )
    assert price_usage(cached) == Decimal("0.1103404")


def test_small_charges_are_not_rounded_per_request() -> None:
    counts = UsageCounters(
        input_tokens=1, cached_tokens=0, cache_write_tokens=0, output_tokens=1
    )
    assert price_usage(counts) == Decimal("0.0000014")
    result = summarize_costs(
        {"first": price_usage(counts), "retry": price_usage(counts)}
    )
    assert result.known_usd == Decimal("0.0000028")
    assert result.total_usd == result.known_usd


def test_explicit_zero_differs_from_missing_or_unpriced_usage() -> None:
    zero = UsageCounters(
        input_tokens=0, cached_tokens=0, cache_write_tokens=0, output_tokens=0
    )
    assert price_usage(zero) == Decimal(0)
    assert price_usage(UsageCounters()) is None
    for missing in (
        "input_tokens",
        "cached_tokens",
        "cache_write_tokens",
        "output_tokens",
    ):
        values = dict(
            input_tokens=10, cached_tokens=0, cache_write_tokens=0, output_tokens=10
        )
        values.pop(missing)
        assert price_usage(UsageCounters(**values)) is None
    assert price_usage(zero, model="unknown") is None
    assert price_usage(zero, service_tier="batch") is None
    summary = summarize_costs({"observed": Decimal("0.01"), "timed-out": None})
    assert summary.known_usd == Decimal("0.01")
    assert summary.total_usd is None
    assert summary.unpriced_attempts == 1
    assert summary.attempts == 2


@pytest.mark.parametrize("value", [-1, True, 1.5, "1"])
def test_invalid_counter_types_are_rejected(value: object) -> None:
    with pytest.raises(ValidationError):
        UsageCounters.model_validate({"input_tokens": value})


@pytest.mark.parametrize(
    "values",
    [
        {"input_tokens": 10, "cached_tokens": 11},
        {"input_tokens": 10, "cache_write_tokens": 11},
        {"input_tokens": 10, "cached_tokens": 6, "cache_write_tokens": 5},
        {"output_tokens": 10, "reasoning_tokens": 11},
    ],
)
def test_inconsistent_subtotals_are_rejected(values: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        UsageCounters.model_validate(values)
