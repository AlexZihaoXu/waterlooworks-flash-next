"""Primitive timing inputs only; no network or platform responses."""

import pytest

from waterlooworks_flash_next.detail_fetch import (
    BurstPacer,
    retry_after_seconds,
    retry_delay,
)


def test_bursts_count_all_attempts_and_add_fifth_burst_rest() -> None:
    pacer = BurstPacer(jitter=lambda low, high: (low + high) / 2)
    for attempt in range(1, 51):
        pacer.finished(100.0)
        expected = 2.3 if attempt == 50 else 0.8 if attempt % 10 == 0 else 0
        assert pacer.wait_seconds(100.0) == pytest.approx(expected)
    assert pacer.attempts == 50
    assert pacer.bursts == 5


def test_processing_time_counts_and_retry_never_shortens_burst_pause() -> None:
    pacer = BurstPacer(jitter=lambda low, high: high)
    for _ in range(10):
        pacer.finished(20.0)
    assert pacer.wait_seconds(20.2) == pytest.approx(0.8)
    assert pacer.wait_seconds(21.0) == 0
    assert pacer.wait_seconds(20.0, retry_not_before=25.0) == 5
    assert pacer.wait_seconds(26.0, retry_not_before=25.0) == 0


def test_retry_ladder_and_bounded_retry_after() -> None:
    assert [retry_delay(i) for i in (1, 2, 3)] == [5, 10, 15]
    with pytest.raises(ValueError):
        retry_delay(4)
    assert retry_after_seconds("60", now=0) == 60
    assert retry_after_seconds("Thu, 01 Jan 1970 00:02:00 GMT", now=60) == 60
    assert retry_after_seconds("invalid", now=0) is None
    assert retry_after_seconds("-1", now=0) is None
