"""One shared, conservative WaterlooWorks request gate per refresh run.

Every paced WaterlooWorks HTTP request (listing pages, detail markup and the
bootstrap metadata/markup reads) goes through a single ``RequestGate``:

* strictly sequential (overlap raises), with a uniformly jittered 3-6 s gap
  measured from the previous response's completion to the next start;
* a 45-120 s rest after every 20-30 requests (jittered threshold);
* a per-run request cap and the remaining rolling 24-hour budget, both stopping
  cleanly *before* the network with ``RequestBudgetExhausted``;
* fail fast: HTTP 429 stops at once and blocks refreshes for max(Retry-After,
  1 h); 401/403, a login/SSO/Duo redirect or a captcha/unusual-activity page
  stops at once and blocks for 6 h. A single transient timeout/5xx is retried
  once after at least 30 s; if that retry fails too the run stops and blocks
  for 6 h (stricter than three consecutive failures). Retries count against
  both caps.

Configuration comes from the server environment only and fails closed with a
fixed message (never echoing the rejected value). No network access here.
"""

import math
import os
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from waterlooworks_flash_next.listings import (
    MAX_RETRY_AFTER_SECONDS,
    CollectionInterrupted,
    FailureCategory,
    ListingFetchError,
)
from waterlooworks_flash_next.ww_signals import (
    RATE_LIMIT_BLOCK_SECONDS,
    UNHAPPY_BLOCK_SECONDS,
    login_redirect,
    unhappy_page,
)

__all__ = [
    "RATE_LIMIT_BLOCK_SECONDS",
    "UNHAPPY_BLOCK_SECONDS",
    "login_redirect",
    "unhappy_page",
]

DEFAULT_REQUEST_CAP = 400
DEFAULT_DAILY_REQUEST_CAP = 1200
DEFAULT_MIN_INTERVAL_MINUTES = 30
MIN_GAP_SECONDS = 3.0
MAX_GAP_SECONDS = 6.0
REST_EVERY_MIN = 20
REST_EVERY_MAX = 30
REST_MIN_SECONDS = 45.0
REST_MAX_SECONDS = 120.0
TRANSIENT_BACKOFF_SECONDS = 30.0
# One retry per request; the second consecutive transient failure stops the run.
MAX_CONSECUTIVE_TRANSIENT = 2
# Browser-originated WaterlooWorks page loads counted per run before paced
# requests (conservatively over-counted): the sign-in landing page, the
# post-sign-in dashboard, the job-board navigation and the captured page-1
# listing response.
BROWSER_REQUESTS = 4
# A refresh is refused unless the rolling budget covers the browser loads plus at
# least one paced request; starting would otherwise only trigger a Duo push.
MIN_RUN_REQUESTS = BROWSER_REQUESTS + 1

StopReason = Literal["request_cap", "daily_cap"]
WaitKind = Literal["rest", "retry"]
# Reported before a rest/retry sleep, then ``None`` once the wait is over.
WaitReport = Callable[[WaitKind | None, float], None]


class PacingConfigError(ValueError):
    """Fixed, value-free configuration error; startup fails closed."""


def _bounded(
    environ: Mapping[str, str], name: str, default: int, high: int, *, low: int
) -> int:
    value = environ.get(name)
    if value is None:
        return default
    if (
        not value.isascii()
        or not value.isdigit()
        or len(value) > len(str(high))
        or not low <= int(value) <= high
    ):
        raise PacingConfigError(f"{name} must be an integer from {low} to {high}.")
    return int(value)


@dataclass(frozen=True)
class PacingConfig:
    request_cap: int = DEFAULT_REQUEST_CAP
    daily_request_cap: int = DEFAULT_DAILY_REQUEST_CAP
    min_interval_minutes: int = DEFAULT_MIN_INTERVAL_MINUTES

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, str] | None = None
    ) -> "PacingConfig":
        env = os.environ if environ is None else environ
        return cls(
            _bounded(env, "WW_REFRESH_REQUEST_CAP", DEFAULT_REQUEST_CAP, 2000, low=1),
            _bounded(
                env,
                "WW_REFRESH_DAILY_REQUEST_CAP",
                DEFAULT_DAILY_REQUEST_CAP,
                5000,
                low=1,
            ),
            _bounded(
                env,
                "WW_REFRESH_MIN_INTERVAL_MINUTES",
                DEFAULT_MIN_INTERVAL_MINUTES,
                1440,
                low=0,
            ),
        )


class RequestBudgetExhausted(Exception):
    """Clean stop before the network: save progress, finish partial, no failure."""

    def __init__(self, reason: StopReason) -> None:
        super().__init__(
            "Per-run WaterlooWorks request cap reached."
            if reason == "request_cap"
            else "Rolling 24-hour WaterlooWorks request budget reached."
        )
        self.reason: StopReason = reason


def block_seconds(error: ListingFetchError) -> int | None:
    """How long manual refreshes stay blocked after this failure, if at all."""
    if error.block_seconds is not None:
        return min(error.block_seconds, MAX_RETRY_AFTER_SECONDS)
    if error.category == FailureCategory.RATE_LIMIT:
        if error.status == 429:
            return min(
                max(error.retry_after or 0, RATE_LIMIT_BLOCK_SECONDS),
                MAX_RETRY_AFTER_SECONDS,
            )
        return UNHAPPY_BLOCK_SECONDS  # challenge / unusual-activity page body
    if error.category == FailureCategory.AUTH:
        return UNHAPPY_BLOCK_SECONDS  # 401/403 or sign-in redirect/page mid-run
    return None


def _noop() -> None:
    return None


class RequestGate:
    """Per-run sequential gate; deadlines are measured from response completion."""

    def __init__(
        self,
        config: PacingConfig,
        *,
        daily_remaining: int,
        record_request: Callable[[], None] | None = None,
        report_wait: WaitReport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
        threshold: Callable[[int, int], int] = random.randint,
        should_stop: Callable[[], bool] | None = None,
    ) -> None:
        self.config = config
        self._should_stop = should_stop
        daily = max(0, daily_remaining)
        self.budget = min(config.request_cap, daily)
        self.stop_reason: StopReason = (
            "request_cap" if config.request_cap <= daily else "daily_cap"
        )
        self._record = record_request or _noop
        # Reassigned per phase so listing and detail progress show their stage.
        self.report_wait: WaitReport = report_wait or (lambda kind, seconds: None)
        self._clock = clock
        self._sleep = sleep
        self._jitter = jitter
        self._threshold = threshold
        self.requests = 0
        self.retries = 0
        self.rests = 0
        self.not_before = float("-inf")
        self.last_finished = float("-inf")
        self.since_rest = 0
        self.rest_after = threshold(REST_EVERY_MIN, REST_EVERY_MAX)
        self.consecutive_transient = 0
        self.in_flight = False

    @property
    def remaining(self) -> int:
        return max(0, self.budget - self.requests)

    def expected_seconds_per_request(self, request_seconds: float) -> float:
        """Mean wall time per request: duration + mean gap + amortized rest."""
        gap = (MIN_GAP_SECONDS + MAX_GAP_SECONDS) / 2
        rest = (REST_MIN_SECONDS + REST_MAX_SECONDS) / 2
        every = (REST_EVERY_MIN + REST_EVERY_MAX) / 2
        return request_seconds + gap + rest / every

    def _finished(self, finished: float) -> None:
        self.last_finished = finished
        self.not_before = finished + self._jitter(MIN_GAP_SECONDS, MAX_GAP_SECONDS)
        self.since_rest += 1

    def note_external(self, finished_at: float, count: int = 1) -> None:
        """Count browser-originated WaterlooWorks loads that already completed.

        An unknown completion time (not finite) conservatively means "just now".
        """
        if not math.isfinite(finished_at):
            finished_at = self._clock()
        for _ in range(count):
            self._record()
            self.requests += 1
        self._finished(finished_at)
        self.since_rest += count - 1

    def request[T](
        self,
        operation: Callable[[], T],
        *,
        allow_rest: bool = True,
        before_request: Callable[[bool], None] | None = None,
    ) -> T:
        if self.in_flight:
            raise RuntimeError("WaterlooWorks requests must never overlap.")
        self.in_flight = True  # Covers waits too: concurrency is exactly one.
        try:
            return self._request(operation, allow_rest, before_request)
        finally:
            self.in_flight = False

    def rest_if_due(self) -> None:
        """Take a due rest now, e.g. before a bootstrap that must not rest inside."""
        if self.in_flight:
            raise RuntimeError("WaterlooWorks requests must never overlap.")
        if self.since_rest < self.rest_after:
            return
        self.in_flight = True
        try:
            now = self._clock()
            rest = (
                self.last_finished
                + self._jitter(REST_MIN_SECONDS, REST_MAX_SECONDS)
                - now
            )
            self._wait("rest", max(rest, self.not_before - now))
            self.rests += 1
            self.since_rest = 0
            self.rest_after = self._threshold(REST_EVERY_MIN, REST_EVERY_MAX)
        finally:
            self.in_flight = False

    def _wait(self, kind: WaitKind | None, seconds: float) -> None:
        if seconds <= 0:
            return
        if kind is not None:
            self.report_wait(kind, seconds)
        self._sleep(seconds)
        if self._should_stop is not None and self._should_stop():
            raise CollectionInterrupted
        if kind is not None:
            self.report_wait(None, 0.0)

    def _request[T](
        self,
        operation: Callable[[], T],
        allow_rest: bool,
        before_request: Callable[[bool], None] | None,
    ) -> T:
        retry_not_before = float("-inf")
        attempt = 0
        while True:
            if self.requests >= self.budget:
                raise RequestBudgetExhausted(self.stop_reason)
            now = self._clock()
            wait = max(0.0, self.not_before - now, retry_not_before - now)
            kind: WaitKind | None = "retry" if attempt else None
            rest = allow_rest and self.since_rest >= self.rest_after
            if rest:
                rest_wait = (
                    self.last_finished
                    + self._jitter(REST_MIN_SECONDS, REST_MAX_SECONDS)
                    - now
                )
                if rest_wait >= wait:
                    wait, kind = rest_wait, "rest"
            self._wait(kind, wait)
            if rest:
                self.rests += 1
                self.since_rest = 0
                self.rest_after = self._threshold(REST_EVERY_MIN, REST_EVERY_MAX)
            if before_request is not None:
                before_request(attempt > 0)
            self._record()
            self.requests += 1
            self.retries += int(attempt > 0)
            error: ListingFetchError | None = None
            try:
                value = operation()
            except ListingFetchError as caught:
                error = caught
            finally:
                self._finished(self._clock())
            if error is None:
                self.consecutive_transient = 0
                return value
            blocked = block_seconds(error)
            if blocked is not None:
                error.block_seconds = blocked
                raise error
            if not error.retryable:
                self.consecutive_transient = 0
                raise error
            self.consecutive_transient += 1
            if attempt or self.consecutive_transient >= MAX_CONSECUTIVE_TRANSIENT:
                error.block_seconds = UNHAPPY_BLOCK_SECONDS
                raise error
            retry_not_before = self.last_finished + TRANSIENT_BACKOFF_SECONDS
            attempt += 1
