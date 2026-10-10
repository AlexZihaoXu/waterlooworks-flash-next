"""Shared WaterlooWorks request gate with injected clock/sleep/jitter only.

Operations are primitive callables; no HTTP fixtures, postings or platform
responses are fabricated.
"""

from collections.abc import Callable

import pytest

from waterlooworks_flash_next.listings import FailureCategory, ListingFetchError
from waterlooworks_flash_next.ww_pacing import (
    MAX_GAP_SECONDS,
    MIN_GAP_SECONDS,
    RATE_LIMIT_BLOCK_SECONDS,
    UNHAPPY_BLOCK_SECONDS,
    PacingConfig,
    PacingConfigError,
    RequestBudgetExhausted,
    RequestGate,
    block_seconds,
    login_redirect,
    unhappy_page,
)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0
        self.sleeps.append(seconds)
        self.now += seconds


def gate(
    clock: Clock,
    *,
    config: PacingConfig | None = None,
    daily_remaining: int = 10_000,
    jitter: Callable[[float, float], float] = lambda low, high: low,
    threshold: Callable[[int, int], int] = lambda low, high: low,
    waits: list[tuple[str | None, float]] | None = None,
    recorded: list[int] | None = None,
) -> RequestGate:
    return RequestGate(
        config or PacingConfig(),
        daily_remaining=daily_remaining,
        record_request=(lambda: recorded.append(1)) if recorded is not None else None,
        report_wait=(lambda kind, seconds: waits.append((kind, seconds)))
        if waits is not None
        else None,
        clock=clock,
        sleep=clock.sleep,
        jitter=jitter,
        threshold=threshold,
    )


def test_defaults_are_conservative_without_environment() -> None:
    config = PacingConfig.from_environment({})
    assert config == PacingConfig(400, 1200, 30)
    assert (MIN_GAP_SECONDS, MAX_GAP_SECONDS) == (3.0, 6.0)


@pytest.mark.parametrize(
    ("name", "low", "high"),
    [
        ("WW_REFRESH_REQUEST_CAP", 1, 2000),
        ("WW_REFRESH_DAILY_REQUEST_CAP", 1, 5000),
        ("WW_REFRESH_MIN_INTERVAL_MINUTES", 0, 1440),
    ],
)
def test_environment_is_validated_strictly_and_fails_closed(
    name: str, low: int, high: int
) -> None:
    assert (
        getattr(
            PacingConfig.from_environment({name: str(low)}),
            {
                "WW_REFRESH_REQUEST_CAP": "request_cap",
                "WW_REFRESH_DAILY_REQUEST_CAP": "daily_request_cap",
                "WW_REFRESH_MIN_INTERVAL_MINUTES": "min_interval_minutes",
            }[name],
        )
        == low
    )
    PacingConfig.from_environment({name: str(high)})
    for bad in ("", " 5", "+5", "-1", "1.5", "0x10", "١٢", str(high + 1), "9" * 30):
        with pytest.raises(PacingConfigError) as error:
            PacingConfig.from_environment({name: bad})
        # Fixed message: never echoes the rejected value.
        assert str(error.value) == (f"{name} must be an integer from {low} to {high}.")
    if low == 1:
        with pytest.raises(PacingConfigError):
            PacingConfig.from_environment({name: "0"})


def test_jittered_gap_is_measured_from_completion_for_every_request() -> None:
    clock = Clock()
    gaps = iter([3.0, 6.0, 4.5])
    pacing = gate(clock, jitter=lambda low, high: next(gaps))

    def operation() -> str:
        clock.now += 0.5  # request duration
        return "ok"

    for _ in range(3):
        assert pacing.request(operation) == "ok"
    # First request has no predecessor; later ones wait the jittered gap.
    assert clock.sleeps == pytest.approx([3.0, 6.0])
    assert pacing.requests == 3


def test_gap_jitter_range_is_three_to_six_seconds() -> None:
    clock = Clock()
    ranges: list[tuple[float, float]] = []

    def jitter(low: float, high: float) -> float:
        ranges.append((low, high))
        return low

    pacing = gate(clock, jitter=jitter)
    pacing.request(lambda: None)
    assert ranges == [(3.0, 6.0)]


def test_rest_break_after_jittered_threshold_and_reports_rest() -> None:
    clock = Clock()
    waits: list[tuple[str | None, float]] = []
    thresholds: list[tuple[int, int]] = []

    def threshold(low: int, high: int) -> int:
        thresholds.append((low, high))
        return 20

    def jitter(low: float, high: float) -> float:
        return 60.0 if (low, high) == (45.0, 120.0) else 3.0

    pacing = gate(clock, jitter=jitter, threshold=threshold, waits=waits)
    for _ in range(21):
        pacing.request(lambda: None)
    assert thresholds[0] == (20, 30)
    assert waits == [("rest", 60.0), (None, 0.0)]
    assert clock.sleeps.count(60.0) == 1
    assert pacing.rests == 1


def test_rest_is_deferred_where_not_allowed_then_taken() -> None:
    clock = Clock()
    waits: list[tuple[str | None, float]] = []
    pacing = gate(
        clock,
        threshold=lambda low, high: 2,
        jitter=lambda low, high: 50.0 if low == 45.0 else 3.0,
        waits=waits,
    )
    pacing.request(lambda: None)
    pacing.request(lambda: None)
    pacing.request(lambda: None, allow_rest=False)
    assert waits == []
    pacing.request(lambda: None)
    assert waits == [("rest", 50.0), (None, 0.0)]


def test_overlapping_requests_are_refused() -> None:
    clock = Clock()
    pacing = gate(clock)

    def nested() -> None:
        pacing.request(lambda: None)

    with pytest.raises(RuntimeError, match="never overlap"):
        pacing.request(nested)


def test_per_run_cap_stops_cleanly_before_network() -> None:
    clock = Clock()
    recorded: list[int] = []
    pacing = gate(clock, config=PacingConfig(3, 1200, 30), recorded=recorded)
    calls: list[int] = []
    for _ in range(3):
        pacing.request(lambda: calls.append(1))
    with pytest.raises(RequestBudgetExhausted) as stop:
        pacing.request(lambda: calls.append(1))
    assert stop.value.reason == "request_cap"
    assert len(calls) == 3 and len(recorded) == 3


def test_daily_remaining_budget_is_the_binding_cap() -> None:
    clock = Clock()
    pacing = gate(clock, daily_remaining=2)
    pacing.note_external(clock(), count=1)
    pacing.request(lambda: None)
    with pytest.raises(RequestBudgetExhausted) as stop:
        pacing.request(lambda: None)
    assert stop.value.reason == "daily_cap"
    assert pacing.remaining == 0


def test_external_browser_request_counts_and_sets_the_gap() -> None:
    clock = Clock()
    recorded: list[int] = []
    pacing = gate(clock, recorded=recorded)
    pacing.note_external(clock.now - 1.0, count=2)
    pacing.request(lambda: None)
    assert recorded == [1, 1, 1]
    assert clock.sleeps == pytest.approx([2.0])


@pytest.mark.parametrize("status", [429])
def test_rate_limit_stops_without_retry_and_blocks_at_least_an_hour(
    status: int,
) -> None:
    clock = Clock()
    calls: list[int] = []

    def operation() -> None:
        calls.append(1)
        raise ListingFetchError(
            "x", FailureCategory.RATE_LIMIT, status=status, retry_after=60
        )

    pacing = gate(clock)
    with pytest.raises(ListingFetchError) as error:
        pacing.request(operation)
    assert calls == [1]
    assert error.value.block_seconds == RATE_LIMIT_BLOCK_SECONDS


def test_longer_retry_after_is_honoured() -> None:
    error = ListingFetchError(
        "x", FailureCategory.RATE_LIMIT, status=429, retry_after=7200
    )
    assert block_seconds(error) == 7200
    assert (
        block_seconds(ListingFetchError("x", FailureCategory.RATE_LIMIT, status=429))
        == RATE_LIMIT_BLOCK_SECONDS
    )


@pytest.mark.parametrize(
    "error",
    [
        ListingFetchError("x", FailureCategory.AUTH, status=403),
        ListingFetchError("x", FailureCategory.AUTH, status=401),
        ListingFetchError("x", FailureCategory.AUTH, status=302),
        ListingFetchError("x", FailureCategory.RATE_LIMIT),  # challenge page body
    ],
)
def test_unhappy_signals_stop_immediately_and_block_six_hours(
    error: ListingFetchError,
) -> None:
    clock = Clock()
    calls: list[int] = []

    def operation() -> None:
        calls.append(1)
        raise error

    with pytest.raises(ListingFetchError) as raised:
        gate(clock).request(operation)
    assert calls == [1]
    assert raised.value.block_seconds == UNHAPPY_BLOCK_SECONDS == 6 * 3600


def test_other_failures_do_not_block() -> None:
    for category in (
        FailureCategory.SCHEMA,
        FailureCategory.REDIRECT,
        FailureCategory.TLS,
        FailureCategory.HTTP,
    ):
        assert block_seconds(ListingFetchError("x", category, status=400)) is None


def test_single_transient_failure_retries_after_thirty_seconds_and_counts() -> None:
    clock = Clock()
    waits: list[tuple[str | None, float]] = []
    recorded: list[int] = []
    results = iter([True, False])

    def operation() -> str:
        if next(results):
            raise ListingFetchError(
                "x", FailureCategory.HTTP, status=503, retryable=True
            )
        return "ok"

    pacing = gate(clock, waits=waits, recorded=recorded)
    assert pacing.request(operation) == "ok"
    assert waits == [("retry", 30.0), (None, 0.0)]
    assert pacing.retries == 1
    assert len(recorded) == 2


def test_failed_retry_stops_at_once_and_blocks_six_hours() -> None:
    """One retry per request; a second consecutive failure never retries again."""
    clock = Clock()
    waits: list[tuple[str | None, float]] = []
    calls: list[int] = []

    def operation() -> None:
        calls.append(1)
        raise ListingFetchError("x", FailureCategory.TIMEOUT, retryable=True)

    pacing = gate(clock, waits=waits)
    with pytest.raises(ListingFetchError) as error:
        pacing.request(operation)
    assert len(calls) == 2
    assert [kind for kind, _ in waits] == ["retry", None]
    assert waits[0][1] >= 30
    assert error.value.block_seconds == UNHAPPY_BLOCK_SECONDS


def test_retries_count_against_the_cap() -> None:
    clock = Clock()
    calls: list[int] = []

    def operation() -> None:
        calls.append(1)
        raise ListingFetchError("x", FailureCategory.TIMEOUT, retryable=True)

    pacing = gate(clock, config=PacingConfig(1, 1200, 30))
    with pytest.raises(RequestBudgetExhausted):
        pacing.request(operation)
    assert pacing.requests == 1 and calls == [1]


def test_waits_count_as_in_flight_so_nothing_can_overlap_a_sleep() -> None:
    clock = Clock()
    pacing = gate(clock, jitter=lambda low, high: 5.0)
    pacing.request(lambda: None)

    def sleep(seconds: float) -> None:
        with pytest.raises(RuntimeError, match="never overlap"):
            pacing.request(lambda: None)
        clock.now += seconds

    pacing._sleep = sleep
    pacing.request(lambda: None)
    assert pacing.requests == 2


def test_stop_request_during_a_wait_interrupts_before_the_network() -> None:
    from waterlooworks_flash_next.listings import CollectionInterrupted

    clock = Clock()
    calls: list[int] = []
    pacing = RequestGate(
        PacingConfig(),
        daily_remaining=100,
        clock=clock,
        sleep=clock.sleep,
        jitter=lambda low, high: low,
        threshold=lambda low, high: low,
        should_stop=lambda: True,
    )
    pacing.request(lambda: calls.append(1))
    with pytest.raises(CollectionInterrupted):
        pacing.request(lambda: calls.append(1))
    assert calls == [1] and pacing.requests == 1


def test_unknown_browser_completion_time_counts_as_now() -> None:
    clock = Clock()
    pacing = gate(clock)
    pacing.note_external(float("-inf"), count=4)
    pacing.request(lambda: None)
    assert pacing.requests == 5
    assert clock.sleeps == pytest.approx([3.0])


def test_success_resets_the_consecutive_transient_counter() -> None:
    clock = Clock()
    sequence = iter([True, False, True, False, True, False])

    def operation() -> None:
        if next(sequence):
            raise ListingFetchError("x", FailureCategory.TIMEOUT, retryable=True)

    pacing = gate(clock)
    for _ in range(3):
        pacing.request(operation)
    assert pacing.requests == 6


def test_expected_seconds_per_request_include_gap_and_amortized_rests() -> None:
    pacing = gate(Clock())
    assert pacing.expected_seconds_per_request(0.5) == pytest.approx(
        0.5 + 4.5 + 82.5 / 25
    )


@pytest.mark.parametrize(
    "location",
    [
        "https://adfs.uwaterloo.ca/adfs/ls/?SAMLRequest=x",
        "https://api-1.duosecurity.com/frame",
        "https://waterlooworks.uwaterloo.ca/notLoggedIn.htm",
        "/home/login.htm",
        "https://waterlooworks.uwaterloo.ca/sso/start",
    ],
)
def test_login_redirects_are_recognized(location: str) -> None:
    assert login_redirect(location)


@pytest.mark.parametrize(
    "location", [None, "", "https://waterlooworks.uwaterloo.ca/myAccount/x.htm"]
)
def test_ordinary_redirects_are_not_login(location: str | None) -> None:
    assert not login_redirect(location)


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("<html>Please complete the CAPTCHA</html>", "rate_limit"),
        ("We detected unusual activity from your account", "rate_limit"),
        ("429 Too Many Requests", "rate_limit"),
        ('<form action="https://adfs.uwaterloo.ca/adfs/ls/">', "sign_in"),
        ("Your session has expired. Please log in.", "sign_in"),
        ("<div>ordinary text</div>", None),
    ],
)
def test_unhappy_page_markers(text: str, kind: str | None) -> None:
    assert unhappy_page(text) == kind


def test_a_due_rest_can_be_taken_before_a_rest_free_bootstrap() -> None:
    clock = Clock()
    waits: list[tuple[str | None, float]] = []
    pacing = gate(
        clock,
        threshold=lambda low, high: 2,
        jitter=lambda low, high: 50.0 if low == 45.0 else 3.0,
        waits=waits,
    )
    pacing.rest_if_due()
    assert waits == []
    pacing.request(lambda: None)
    pacing.request(lambda: None)
    pacing.rest_if_due()
    assert waits == [("rest", 50.0), (None, 0.0)]
    pacing.request(lambda: None, allow_rest=False)
    pacing.request(lambda: None, allow_rest=False)
    assert pacing.rests == 1 and waits[-1] == (None, 0.0)
