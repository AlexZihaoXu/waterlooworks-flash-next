"""Sequential runtime-only detail transport through the shared gate; checkpoints."""

import errno
import json
import socket
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.client import HTTPException, IncompleteRead, RemoteDisconnected
from ssl import SSLError
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request

from waterlooworks_flash_next.content import (
    ContentError,
    StoredPostingContent,
    StoredPostingFormatting,
    extract_content_with_formatting,
)
from waterlooworks_flash_next.content_storage import (
    complete_content_attempt,
    fail_content_attempt,
    interrupt_content_attempt,
    record_content_request,
    start_content_attempt,
)
from waterlooworks_flash_next.database import StoredListing
from waterlooworks_flash_next.listings import (
    LISTING_URL,
    MAX_RETRY_AFTER_SECONDS,
    CollectionInterrupted,
    FailureCategory,
    ListingFetchError,
    build_direct_opener,
    decode_response_text,
    http_failure,
    unexpected_body_failure,
)
from waterlooworks_flash_next.listings import (
    retry_after_seconds as retry_after_seconds,  # re-exported for existing importers
)
from waterlooworks_flash_next.ww_pacing import (
    RequestBudgetExhausted,
    RequestGate,
    WaitKind,
    block_seconds,
)

MAX_DETAIL_BYTES = 5 * 1024 * 1024
# Deterministic per-posting response problems: record and move to the next posting.
# Every other category means the session/transport is unusable and stops the pass.
ITEM_FAILURE_CATEGORIES = frozenset(
    {FailureCategory.SCHEMA, FailureCategory.SIZE, FailureCategory.ENCODING}
)


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
            retry_after=retry_after,
        )


def record_blocked_until(
    connection: sqlite3.Connection, run_id: str, retry_after: int
) -> None:
    """Durably block manual refreshes until now + the clamped block duration.

    Shared by listing and detail phases (``ww_pacing.block_seconds`` decides the
    duration). Clear a stale block with
    `python -m waterlooworks_flash_next.rate_limit clear`.
    """
    until = datetime.now(UTC) + timedelta(
        seconds=min(max(retry_after, 0), MAX_RETRY_AFTER_SECONDS)
    )
    with connection:
        connection.execute(
            "UPDATE collection_runs SET blocked_until=? WHERE id=?",
            (until.isoformat(), run_id),
        )


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


@dataclass(frozen=True)
class DetailProgress:
    stage: str
    completed: int
    total: int
    requests: int
    retries: int
    rests: int
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
    first_formatting: StoredPostingFormatting | None = None

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
            with build_direct_opener().open(request, timeout=30) as response:
                if response.status != 200:
                    raise DetailFetchError(FailureCategory.HTTP, status=response.status)
                if response.headers.get("Content-Encoding", "identity") != "identity":
                    raise DetailFetchError(FailureCategory.ENCODING)
                raw = response.read(MAX_DETAIL_BYTES + 1)
                if len(raw) > MAX_DETAIL_BYTES:
                    raise DetailFetchError(FailureCategory.SIZE)
                return decode_response_text(raw, response.headers.get_content_charset())
        except HTTPError as error:
            headers = error.headers
            failure = http_failure(
                error.code,
                retry_after_seconds(
                    headers.get("Retry-After") if headers else None, now=time.time()
                ),
                headers.get("Location") if headers else None,
            )
            error.close()
            raise DetailFetchError(
                failure.category,
                status=failure.status,
                retry_after=failure.retry_after,
                retryable=failure.retryable,
            ) from None
        except DetailFetchError:
            raise
        except ListingFetchError:
            raise DetailFetchError(FailureCategory.ENCODING) from None
        except (URLError, OSError, HTTPException) as error:
            raise transport_failure(error) from None
        except Exception:
            raise DetailFetchError(FailureCategory.INTERNAL) from None


def unexpected_detail_body(text: str) -> DetailFetchError | None:
    """A detail response that failed validation and looks like a block/sign-in."""
    failure = unexpected_body_failure(text)
    return None if failure is None else DetailFetchError(failure.category)


class DetailCollection:
    """One explicit list-derived queue. Each success is a durable checkpoint.

    Every request goes through the run's shared ``RequestGate``. A posting whose
    own response fails validation is recorded as a failed attempt and skipped;
    session-level failures, a failed bootstrap, or a run of consecutive item
    failures (suggesting a broken session) stop the pass. A pacing cap or a
    shutdown stops cleanly before the network and leaves the rest queued.
    """

    MAX_CONSECUTIVE_ITEM_FAILURES = 10
    # Title-click bootstrap: one metadata read plus one markup read.
    BOOTSTRAP_REQUESTS = 2

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
        should_stop: Callable[[], bool] | None = None,
        *,
        gate: RequestGate,
    ) -> None:
        self.source = source
        self.should_stop = should_stop
        self.bootstrap = bootstrap
        self.connection = connection
        self.run_id = run_id
        self.postings = postings
        self.progress = progress
        self.gate = gate
        gate.report_wait = self._wait
        self.completed = 0
        self.failed = 0
        self.requests = 0
        self.retries = 0

    def _progress(
        self, stage: str = "details", wait: float = 0.0, *, starting: bool = False
    ) -> None:
        self.progress(
            DetailProgress(
                stage,
                self.completed,
                len(self.postings),
                self.requests,
                self.retries,
                self.gate.rests,
                wait,
                self.failed,
            )
        )

    def _wait(self, kind: WaitKind | None, seconds: float) -> None:
        if kind is None:
            self._progress()
        else:
            self._progress(f"detail_{kind}", seconds)

    def _operation(
        self,
        attempt_id: str,
        operation: Callable[[], str],
        *,
        allow_rest: bool = True,
    ) -> str:
        def before(retry: bool) -> None:
            record_content_request(self.connection, attempt_id, retry=retry)
            self.requests += 1
            self.retries += int(retry)
            self._progress(starting=True)

        try:
            return self.gate.request(
                operation, allow_rest=allow_rest, before_request=before
            )
        finally:
            self._progress()

    def _request(self, attempt_id: str, template: int, posting_id: str) -> str:
        assert self.source is not None
        source = self.source
        return self._operation(
            attempt_id, lambda: source.fetch_once(template, posting_id)
        )

    def _needed(self) -> int:
        """Requests the next posting may need, so a cap never splits a bootstrap."""
        if self.source is None:
            return self.BOOTSTRAP_REQUESTS
        if self.source.first_content is not None:
            return 0
        if self.source.markup_index is not None:
            return 1
        return max(1, len(self.source.templates))

    def _content(
        self, attempt_id: str, posting: StoredListing
    ) -> tuple[StoredPostingContent, StoredPostingFormatting | None]:
        if self.source is None:
            if self.bootstrap is None:
                raise DetailFetchError(FailureCategory.SCHEMA)
            # No rest inside the bootstrap: the page waits on its own idle window,
            # so a due rest is taken before the title click.
            self.gate.rest_if_due()
            self.source = self.bootstrap(
                posting,
                lambda operation: self._operation(
                    attempt_id, operation, allow_rest=False
                ),
            )
            self.source.confirm_capture()
            self.source.used = True
        if self.source.first_content is not None:
            content = self.source.first_content
            formatting = self.source.first_formatting
            self.source.first_content = None
            self.source.first_formatting = None
            if content.id != posting.id:
                raise DetailFetchError(FailureCategory.SCHEMA)
            return content, formatting
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
                content, formatting = extract_content_with_formatting(
                    text,
                    posting.id,
                    posting.title,
                    posting.organization,
                    secrets=self.source.secrets,
                )
            except ContentError:
                raise unexpected_detail_body(text) or DetailFetchError(
                    FailureCategory.SCHEMA
                ) from None
            self.source.markup_index = index
            return content, formatting
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
            if self.should_stop is not None and self.should_stop():
                raise CollectionInterrupted
            if self.gate.remaining < self._needed():
                raise RequestBudgetExhausted(self.gate.stop_reason)
            established = self.source is not None
            attempt_id = start_content_attempt(self.connection, self.run_id, posting.id)
            try:
                content, formatting = self._content(attempt_id, posting)
                complete_content_attempt(
                    self.connection, attempt_id, content, formatting
                )
            except (RequestBudgetExhausted, CollectionInterrupted):
                # Clean stop before the network: not a posting failure.
                interrupt_content_attempt(self.connection, attempt_id)
                raise
            except Exception as caught:
                error = (
                    caught
                    if isinstance(caught, DetailFetchError)
                    else DetailFetchError(FailureCategory.INTERNAL)
                )
                blocked = block_seconds(error)
                if blocked is not None:
                    error.block_seconds = blocked
                    record_blocked_until(self.connection, self.run_id, blocked)
                fail_content_attempt(
                    self.connection,
                    attempt_id,
                    error.category.value,
                    http_status=error.status,
                )
                consecutive += 1
                if (
                    blocked is not None
                    or not established
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
