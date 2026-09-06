"""Pure scheduling/progress tests; no credentials, mock postings, or requests."""

import pytest

from waterlooworks_flash_next.listings import (
    FailureCategory,
    attempt_delay,
    batch_pages,
    capture_fresh,
    eta_text,
    http_failure,
    remaining_delay,
)


def test_batches_are_at_most_three_sequential_pages() -> None:
    assert batch_pages(1, 10) == (1, 2, 3)
    assert batch_pages(4, 10) == (4, 5, 6)
    assert batch_pages(10, 10) == (10,)
    assert batch_pages(11, 10) == ()


def test_delay_is_measured_from_previous_completion() -> None:
    assert remaining_delay(10.0, 10.0) == 2.0
    assert remaining_delay(10.0, 10.5) == 1.5
    assert remaining_delay(10.0, 12.0) == 0
    assert remaining_delay(10.0, 13.0) == 0


def test_batch_boundary_does_not_change_request_delay() -> None:
    assert batch_pages(5, 8)[0] == 5
    assert remaining_delay(40.0, 40.2) == pytest.approx(1.8)


def test_eta_includes_remaining_requests_and_intervals() -> None:
    assert eta_text(4, 0.5) == "00:00:10"
    assert eta_text(0, None) == "00:00:00"
    assert eta_text(4, None) == "estimating"
    assert eta_text(1440, 0.5) == "01:00:00"


def test_two_retries_use_completion_based_exponential_delays() -> None:
    assert [attempt_delay(i) for i in (1, 2, 3)] == [2, 4, 8]
    assert remaining_delay(10, 11, attempt_delay(2)) == 3
    assert remaining_delay(20, 21, attempt_delay(3)) == 7
    with pytest.raises(ValueError):
        attempt_delay(4)


@pytest.mark.parametrize("status", [401, 403, 429, 302, 400])
def test_non_transient_http_failures_do_not_retry(status: int) -> None:
    assert not http_failure(status).retryable


@pytest.mark.parametrize("status", [408, 500, 502, 503, 504])
def test_transient_http_failures_allow_bounded_retries(status: int) -> None:
    assert http_failure(status).retryable


def test_safe_http_category_contains_no_response_details() -> None:
    error = http_failure(429)
    assert error.category is FailureCategory.RATE_LIMIT
    assert str(error) == "Listing HTTP 429."
    assert error.status == 429


def test_collection_requires_a_recent_capture() -> None:
    assert capture_fresh(100, 100)
    assert capture_fresh(100, 130)
    assert not capture_fresh(100, 131)
    assert not capture_fresh(float("-inf"), 100)
