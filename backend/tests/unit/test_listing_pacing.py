"""Pure scheduling/progress tests; no credentials, mock postings, or requests."""

from typing import Any, cast

import pytest

from waterlooworks_flash_next.listings import (
    FailureCategory,
    ListingFetchError,
    ListingPage,
    ListingSource,
    batch_pages,
    capture_fresh,
    eta_text,
    http_failure,
    unexpected_body_failure,
)
from waterlooworks_flash_next.ww_pacing import PacingConfig, RequestGate


def test_batches_are_at_most_three_sequential_pages() -> None:
    assert batch_pages(1, 10) == (1, 2, 3)
    assert batch_pages(4, 10) == (4, 5, 6)
    assert batch_pages(10, 10) == (10,)
    assert batch_pages(11, 10) == ()


def test_eta_uses_the_paced_wall_time_per_page() -> None:
    assert eta_text(4, 2.5) == "00:00:10"
    assert eta_text(0, None) == "00:00:00"
    assert eta_text(4, None) == "estimating"
    assert eta_text(1440, 2.5) == "01:00:00"
    gate = RequestGate(PacingConfig(), daily_remaining=10)
    # 0.5 s request + 4.5 s mean gap + 82.5 s mean rest every 25 requests.
    assert eta_text(10, gate.expected_seconds_per_request(0.5)) == "00:01:23"


def source_with_gate(gate: RequestGate | None) -> ListingSource:
    # Runtime-only template slots; no captured form, cookie or response data.
    return ListingSource([], {}, cast(ListingPage, None), 1, 0.0, None, gate=gate)


def test_listing_pages_go_through_the_shared_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [100.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    gate = RequestGate(
        PacingConfig(),
        daily_remaining=10,
        clock=lambda: now[0],
        sleep=sleep,
        jitter=lambda low, high: high,
        threshold=lambda low, high: low,
    )
    source = source_with_gate(gate)
    sentinel = cast(Any, object())
    monkeypatch.setattr(source, "_fetch_once", lambda page: (sentinel, 0.0))
    assert source.fetch(2) == (sentinel, 0.0)
    assert source.fetch(3) == (sentinel, 0.0)
    assert sleeps == [6.0] and gate.requests == 2


def test_listing_fetch_without_a_gate_never_reaches_the_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = source_with_gate(None)
    monkeypatch.setattr(source, "_fetch_once", lambda page: pytest.fail("network"))
    with pytest.raises(ListingFetchError) as error:
        source.fetch(2)
    assert error.value.category is FailureCategory.INTERNAL


def test_redirect_to_sign_in_is_an_authentication_failure() -> None:
    assert http_failure(
        302, location="https://adfs.uwaterloo.ca/adfs/ls/"
    ).category is (FailureCategory.AUTH)
    assert http_failure(302, location="/notLoggedIn.htm").category is (
        FailureCategory.AUTH
    )
    assert http_failure(302, location="/myAccount/x.htm").category is (
        FailureCategory.REDIRECT
    )
    assert http_failure(302).category is FailureCategory.REDIRECT


def test_unexpected_challenge_or_sign_in_body_is_classified() -> None:
    challenge = unexpected_body_failure("<p>Unusual activity detected</p>")
    assert challenge is not None
    assert challenge.category is FailureCategory.RATE_LIMIT
    assert str(challenge) == "Response looked like a rate-limit or challenge page."
    sign_in = unexpected_body_failure("Your session has expired")
    assert sign_in is not None and sign_in.category is FailureCategory.AUTH
    assert unexpected_body_failure("{}") is None


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
