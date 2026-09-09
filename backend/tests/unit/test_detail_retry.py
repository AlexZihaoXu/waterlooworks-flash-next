"""Real retry controller with primitive failing operations; no HTTP fixtures."""

import pytest

from waterlooworks_flash_next.detail_fetch import (
    BurstPacer,
    DetailFetchError,
    paced_request,
)
from waterlooworks_flash_next.listings import FailureCategory


@pytest.mark.parametrize("succeeds", [True, False])
def test_three_retries_measured_from_failure_completion(succeeds: bool) -> None:
    now = [0.0]
    waits: list[float] = []
    attempts: list[bool] = []
    pacer = BurstPacer(jitter=lambda low, high: high, attempts=49)

    def operation() -> None:
        now[0] += 0.1
        if len(attempts) < 4 or not succeeds:
            raise DetailFetchError(FailureCategory.NETWORK, retryable=True)

    def sleep(delay: float) -> None:
        waits.append(delay)
        now[0] += delay

    def run() -> None:
        paced_request(
            operation,
            pacer,
            before_request=attempts.append,
            report_wait=lambda retry, seconds: None,
            after_request=lambda: None,
            clock=lambda: now[0],
            sleep=sleep,
        )

    if succeeds:
        run()
    else:
        with pytest.raises(DetailFetchError):
            run()
    assert attempts == [False, True, True, True]
    assert waits == pytest.approx([5, 10, 15])
    assert now[0] == pytest.approx(30.4)
    assert pacer.attempts == 53


@pytest.mark.parametrize(
    "category",
    [
        FailureCategory.AUTH,
        FailureCategory.RATE_LIMIT,
        FailureCategory.TLS,
        FailureCategory.SCHEMA,
        FailureCategory.REDIRECT,
    ],
)
def test_nontransient_failures_stop_without_sleep_or_retry(
    category: FailureCategory,
) -> None:
    attempts: list[bool] = []
    waits: list[float] = []

    def operation() -> None:
        raise DetailFetchError(category, retry_after=60)

    with pytest.raises(DetailFetchError):
        paced_request(
            operation,
            BurstPacer(),
            before_request=attempts.append,
            report_wait=lambda retry, seconds: None,
            after_request=lambda: None,
            clock=lambda: 0,
            sleep=waits.append,
        )
    assert attempts == [False]
    assert waits == []


def test_successful_final_attempt_does_not_sleep_after_a_burst() -> None:
    waits: list[float] = []
    pacer = BurstPacer(attempts=9)
    paced_request(
        lambda: None,
        pacer,
        before_request=lambda retry: None,
        report_wait=lambda retry, seconds: None,
        after_request=lambda: None,
        clock=lambda: 1,
        sleep=waits.append,
    )
    assert pacer.attempts == 10
    assert waits == []
    assert 0.6 <= pacer.wait_seconds(1) <= 1.0
