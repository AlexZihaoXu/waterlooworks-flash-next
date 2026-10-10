"""Sequential runtime-only detail transport, bounded bursts, and checkpoints."""

import errno
import json
import math
import random
import socket
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from http.client import HTTPException, IncompleteRead, RemoteDisconnected
from ssl import SSLError
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from waterlooworks_flash_next.content import (
    ContentError,
    StoredPostingContent,
    extract_content,
)
from waterlooworks_flash_next.content_storage import (
    complete_content_attempt,
    fail_content_attempt,
    record_content_request,
    start_content_attempt,
)
from waterlooworks_flash_next.database import StoredListing
from waterlooworks_flash_next.listings import (
    LISTING_URL,
    FailureCategory,
    ListingFetchError,
    NoRedirect,
    decode_response_text,
)

MAX_DETAIL_BYTES = 5 * 1024 * 1024
# Deterministic per-posting response problems: record and move to the next posting.
# Every other category means the session/transport is unusable and stops the pass.
ITEM_FAILURE_CATEGORIES = frozenset(
    {FailureCategory.SCHEMA, FailureCategory.SIZE, FailureCategory.ENCODING}
)


def retry_delay(retry: int) -> int:
    if retry not in (1, 2, 3):
        raise ValueError("Only three retries are allowed.")
    return 5 * retry


def retry_after_seconds(value: str | None, *, now: float) -> int | None:
    if value is None:
        return None
    try:
        if value.strip().isascii() and value.strip().isdigit():
            seconds = int(value.strip())
        else:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return None
            seconds = max(0, math.ceil(date.timestamp() - now))
        return seconds if 0 <= seconds <= 2**31 - 1 else None
    except (ValueError, TypeError, OverflowError):
        return None


@dataclass
class BurstPacer:
    """Deadlines are measured from response completion, not request start.

    Sleep only before another attempt. Retry and burst deadlines both apply;
    overlapping waits use their maximum, never shorten either obligation.
    """

    jitter: Callable[[float, float], float] = field(default=random.uniform, repr=False)
    attempts: int = 0
    not_before: float = 0.0

    @property
    def bursts(self) -> int:
        return self.attempts // 10

    def finished(self, now: float) -> None:
        self.attempts += 1
        pause = 0.0
        if self.attempts % 10 == 0:
            pause = self.jitter(0.6, 1.0)
            if self.bursts % 5 == 0:
                pause += self.jitter(1.0, 2.0)
        self.not_before = now + pause

    def wait_seconds(self, now: float, *, retry_not_before: float = 0.0) -> float:
        return max(0.0, self.not_before - now, retry_not_before - now)


class DetailFetchError(ListingFetchError):
    def __init__(
        self,
        category: FailureCategory,
        *,
        status: int | None = None,
        retryable: bool = False,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(
            "Detail request stopped; diagnostics suppressed.",
            category,
            status=status,
            retryable=retryable,
        )
        self.retry_after = retry_after


def transport_failure(error: BaseException) -> DetailFetchError:
    """Only positively recognized transient transport failures may retry."""
    reason = error.reason if isinstance(error, URLError) else error
    if isinstance(reason, SSLError):
        return DetailFetchError(FailureCategory.TLS)
    if isinstance(reason, TimeoutError):
        return DetailFetchError(FailureCategory.TIMEOUT, retryable=True)
    if isinstance(reason, (IncompleteRead, RemoteDisconnected)):
        return DetailFetchError(FailureCategory.TRUNCATED, retryable=True)
    if isinstance(reason, socket.gaierror):
        return DetailFetchError(
            FailureCategory.NETWORK, retryable=reason.errno == socket.EAI_AGAIN
        )
    if isinstance(
        reason, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)
    ):
        return DetailFetchError(FailureCategory.NETWORK, retryable=True)
    if isinstance(reason, HTTPException):
        return DetailFetchError(FailureCategory.SCHEMA)
    if isinstance(reason, OSError):
        if reason.errno in (errno.ETIMEDOUT, 10060):
            return DetailFetchError(FailureCategory.TIMEOUT, retryable=True)
        if reason.errno in (
            errno.ECONNRESET,
            errno.ECONNABORTED,
            errno.EPIPE,
            10053,
            10054,
        ):
            return DetailFetchError(FailureCategory.NETWORK, retryable=True)
    return DetailFetchError(FailureCategory.NETWORK)


def paced_request[T](
    operation: Callable[[], T],
    pacer: BurstPacer,
    *,
    before_request: Callable[[bool], None],
    report_wait: Callable[[bool, float], None],
    after_request: Callable[[], None],
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Shared real request controller, tested with primitive operations/clocks."""
    retry_not_before = 0.0
    for attempt in range(4):
        wait = pacer.wait_seconds(clock(), retry_not_before=retry_not_before)
        if wait:
            report_wait(attempt > 0, wait)
            sleep(wait)
        before_request(attempt > 0)
        error: DetailFetchError | None = None
        try:
            value = operation()
        except DetailFetchError as caught:
            error = caught
        finally:
            finished = clock()
            pacer.finished(finished)
            after_request()
        if error is None:
            return value
        if not error.retryable or attempt == 3:
            raise error
        retry_not_before = finished + retry_delay(attempt + 1)
    raise AssertionError("Unreachable retry state")


@dataclass(frozen=True)
class DetailProgress:
    stage: str
    completed: int
    total: int
    requests: int
    retries: int
    bursts: int
    wait_seconds: float = 0.0
    failed: int = 0


@dataclass(repr=False)
class DetailSource:
    """Opaque forms and cookies live only in this per-run object; never serialize."""

    templates: tuple[tuple[tuple[str, str], ...], ...]
    headers: dict[str, str]
    secrets: tuple[str, ...]
    capture_check: Callable[[], bool]
    markup_index: int | None = None
    used: bool = False
    first_content: StoredPostingContent | None = None

    def confirm_capture(self) -> None:
        if not self.capture_check():
            raise DetailFetchError(FailureCategory.SCHEMA)

    def fetch_once(self, template: int, posting_id: str) -> str:
        body = urlencode(
            [
                (key, posting_id if key == "postingId" else value)
                for key, value in self.templates[template]
            ]
        ).encode("utf-8")
        try:
            request = Request(
                LISTING_URL, data=body, headers=self.headers, method="POST"
            )
            with build_opener(NoRedirect()).open(request, timeout=30) as response:
                if response.status != 200:
                    raise DetailFetchError(FailureCategory.HTTP, status=response.status)
                if response.headers.get("Content-Encoding", "identity") != "identity":
                    raise DetailFetchError(FailureCategory.ENCODING)
                raw = response.read(MAX_DETAIL_BYTES + 1)
                if len(raw) > MAX_DETAIL_BYTES:
                    raise DetailFetchError(FailureCategory.SIZE)
                return decode_response_text(raw, response.headers.get_content_charset())
        except HTTPError as error:
            status = error.code
            delay = retry_after_seconds(
                error.headers.get("Retry-After") if error.headers else None,
                now=time.time(),
            )
            error.close()
            category = (
                FailureCategory.AUTH
                if status in (401, 403)
                else FailureCategory.RATE_LIMIT
                if status == 429
                else FailureCategory.REDIRECT
                if 300 <= status < 400
                else FailureCategory.HTTP
            )
            raise DetailFetchError(
                category,
                status=status,
                retry_after=delay,
                retryable=status in (408, 500, 502, 503, 504),
            ) from None
        except DetailFetchError:
            raise
        except ListingFetchError:
            raise DetailFetchError(FailureCategory.ENCODING) from None
        except (URLError, OSError, HTTPException) as error:
            raise transport_failure(error) from None
        except Exception:
            raise DetailFetchError(FailureCategory.INTERNAL) from None


class DetailCollection:
    """One explicit list-derived queue. Each success is a durable checkpoint.

    A posting whose own response fails validation is recorded as a failed attempt
    and skipped; session-level failures, a failed bootstrap, or a run of
    consecutive item failures (suggesting a broken session) stop the pass.
    """

    MAX_CONSECUTIVE_ITEM_FAILURES = 10

    def __init__(
        self,
        source: DetailSource | None,
        connection: sqlite3.Connection,
        run_id: str,
        postings: list[StoredListing],
        progress: Callable[[DetailProgress], None],
        bootstrap: Callable[
            [StoredListing, Callable[[Callable[[], str]], str]], DetailSource
        ]
        | None = None,
    ) -> None:
        self.source = source
        self.bootstrap = bootstrap
        self.connection = connection
        self.run_id = run_id
        self.postings = postings
        self.progress = progress
        self.pacer = BurstPacer()
        self.completed = 0
        self.failed = 0
        self.retries = 0

    def _progress(
        self, stage: str = "details", wait: float = 0.0, *, starting: bool = False
    ) -> None:
        self.progress(
            DetailProgress(
                stage,
                self.completed,
                len(self.postings),
                self.pacer.attempts + int(starting),
                self.retries,
                self.pacer.bursts,
                wait,
                self.failed,
            )
        )

    def _operation(self, attempt_id: str, operation: Callable[[], str]) -> str:
        def before(retry: bool) -> None:
            record_content_request(self.connection, attempt_id, retry=retry)
            self.retries += int(retry)
            self._progress(starting=True)

        return paced_request(
            operation,
            self.pacer,
            before_request=before,
            report_wait=lambda retry, delay: self._progress(
                "detail_retry" if retry else "detail_rest", delay
            ),
            after_request=self._progress,
        )

    def _request(self, attempt_id: str, template: int, posting_id: str) -> str:
        assert self.source is not None
        source = self.source
        return self._operation(
            attempt_id, lambda: source.fetch_once(template, posting_id)
        )

    def _content(self, attempt_id: str, posting: StoredListing) -> StoredPostingContent:
        if self.source is None:
            if self.bootstrap is None:
                raise DetailFetchError(FailureCategory.SCHEMA)
            self.source = self.bootstrap(
                posting, lambda operation: self._operation(attempt_id, operation)
            )
            self.source.confirm_capture()
            self.source.used = True
        if self.source.first_content is not None:
            content = self.source.first_content
            self.source.first_content = None
            if content.id != posting.id:
                raise DetailFetchError(FailureCategory.SCHEMA)
            return content
        if not posting.title:
            raise DetailFetchError(FailureCategory.SCHEMA)
        candidates = (
            range(len(self.source.templates))
            if self.source.markup_index is None
            else [self.source.markup_index]
        )
        for index in candidates:
            text = self._request(attempt_id, index, posting.id)
            try:
                metadata = json.loads(text)
            except ValueError:
                metadata = None
            else:
                # Only the observed title-click metadata object may be skipped.
                # It is not fabricated, stored as body content, or fetched again.
                if (
                    not isinstance(metadata, dict)
                    or str(metadata.get("id")) != posting.id
                    or not {"position", "org", "div", "divId"} <= metadata.keys()
                    or self.source.markup_index is not None
                ):
                    raise DetailFetchError(FailureCategory.SCHEMA)
                continue
            try:
                content = extract_content(
                    text,
                    posting.id,
                    posting.title,
                    posting.organization,
                    secrets=self.source.secrets,
                )
            except ContentError:
                raise DetailFetchError(FailureCategory.SCHEMA) from None
            self.source.markup_index = index
            return content
        raise DetailFetchError(FailureCategory.SCHEMA)

    def run(self) -> None:
        if self.source is not None:
            self.source.confirm_capture()
            if self.source.used:
                raise DetailFetchError(FailureCategory.STALE)
            self.source.used = True
        self._progress()
        consecutive = 0
        for posting in self.postings:
            established = self.source is not None
            attempt_id = start_content_attempt(self.connection, self.run_id, posting.id)
            try:
                content = self._content(attempt_id, posting)
                complete_content_attempt(self.connection, attempt_id, content)
            except Exception as caught:
                error = (
                    caught
                    if isinstance(caught, DetailFetchError)
                    else DetailFetchError(FailureCategory.INTERNAL)
                )
                if (
                    error.category == FailureCategory.RATE_LIMIT
                    and error.retry_after is not None
                ):
                    until = datetime.now(UTC) + timedelta(seconds=error.retry_after)
                    with self.connection:
                        self.connection.execute(
                            "UPDATE collection_runs SET blocked_until=? WHERE id=?",
                            (until.isoformat(), self.run_id),
                        )
                fail_content_attempt(
                    self.connection,
                    attempt_id,
                    error.category.value,
                    http_status=error.status,
                )
                consecutive += 1
                if (
                    not established
                    or error.category not in ITEM_FAILURE_CATEGORIES
                    or consecutive >= self.MAX_CONSECUTIVE_ITEM_FAILURES
                ):
                    raise error from None
                self.failed += 1
                self._progress()
                continue
            consecutive = 0
            self.completed += 1
            self._progress()
