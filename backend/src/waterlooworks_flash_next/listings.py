"""Fresh-session listing collection with bounded retries; no detail requests."""

import codecs
import math
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import Message
from enum import StrEnum
from http.client import HTTPException
from ssl import SSLError
from typing import IO
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from playwright.sync_api import Page, Response
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from waterlooworks_flash_next.logging import stage_logger

LISTING_URL = "https://waterlooworks.uwaterloo.ca/myAccount/co-op/full/jobs.htm"
INTERVAL_SECONDS = 2.0
BATCH_SIZE = 3
MAX_ATTEMPTS = 3
MAX_CAPTURE_AGE_SECONDS = 30.0
MAX_RESPONSE_BYTES = 20 * 1024 * 1024
FORM_KEYS = {
    "page",
    "sort",
    "itemsPerPage",
    "filters",
    "columns",
    "keyword",
    "action",
    "isDataViewer",
}


class FailureCategory(StrEnum):
    AUTH = "authentication"
    RATE_LIMIT = "rate_limit"
    REDIRECT = "redirect"
    HTTP = "http_status"
    TIMEOUT = "timeout"
    NETWORK = "network"
    TLS = "tls"
    TRUNCATED = "incomplete_response"
    JSON = "invalid_json"
    ENCODING = "response_encoding"
    SCHEMA = "response_schema"
    PAGINATION = "pagination"
    SIZE = "response_size"
    STALE = "stale_capture"
    INTERNAL = "internal"


class ListingFetchError(Exception):
    """Only fixed safe reasons and categories, never underlying exception text."""

    def __init__(
        self,
        message: str,
        category: FailureCategory = FailureCategory.PAGINATION,
        *,
        retryable: bool = False,
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.retryable = retryable
        self.status = status


def http_failure(status: int) -> ListingFetchError:
    category = FailureCategory.HTTP
    if status in (401, 403):
        category = FailureCategory.AUTH
    elif status == 429:
        category = FailureCategory.RATE_LIMIT
    elif 300 <= status < 400:
        category = FailureCategory.REDIRECT
    return ListingFetchError(
        f"Listing HTTP {status}.",
        category,
        retryable=status in (408, 500, 502, 503, 504),
        status=status,
    )


def attempt_delay(attempt: int) -> float:
    if not 1 <= attempt <= MAX_ATTEMPTS:
        raise ValueError("Attempt must be between 1 and 3.")
    return (2.0, 4.0, 8.0)[attempt - 1]


def capture_fresh(finished_at: float, now: float) -> bool:
    return 0 <= now - finished_at <= MAX_CAPTURE_AGE_SECONDS


def decode_response_text(raw: bytes, declared_charset: str | None) -> str:
    """Prefer UTF-8; use declared legacy encoding only on a strict decode failure.

    WaterlooWorks page 8 was verified as ISO-8859-1. Never silently replace bytes
    or guess a charset from posting contents; malformed JSON still fails later.
    """
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        if declared_charset is None:
            raise ValueError
        charset = codecs.lookup(declared_charset).name
        if charset not in ("iso8859-1", "cp1252"):
            raise ValueError
        return raw.decode(charset)
    except (ValueError, LookupError):
        raise ListingFetchError(
            "Response cannot be decoded with an allowed declared charset.",
            FailureCategory.ENCODING,
        ) from None


class ListingCell(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    key: str
    value: JsonValue = Field(repr=False, exclude=True)


class ListingRow(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    data: list[ListingCell] = Field(repr=False, exclude=True)

    def posting_id(self) -> str:
        ids = [cell.value for cell in self.data if cell.key == "Id"]
        if len(ids) != 1 or isinstance(ids[0], bool):
            raise ListingFetchError("Listing row has an invalid posting ID.")
        value = ids[0]
        if not isinstance(value, (str, int)) or not str(value).strip().isdigit():
            raise ListingFetchError("Listing row has an invalid posting ID.")
        return str(value).strip()


class ListingPage(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True, strict=True)
    totalResults: int = Field(ge=0)
    page: int = Field(ge=1)
    data: list[ListingRow] = Field(repr=False, exclude=True)


def batch_pages(start: int, total: int) -> tuple[int, ...]:
    return tuple(range(start, min(start + BATCH_SIZE, total + 1)))


def remaining_delay(
    finished_at: float, now: float, interval: float = INTERVAL_SECONDS
) -> float:
    return max(0.0, interval - (now - finished_at))


def eta_text(remaining: int, average_request_seconds: float | None) -> str:
    if remaining <= 0:
        return "00:00:00"
    if average_request_seconds is None:
        return "estimating"
    seconds = math.ceil(remaining * (average_request_seconds + INTERVAL_SECONDS))
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def listing_response(response: Response) -> bool:
    """Distinguish the observed list POST from detail POSTs to the same route."""
    request = response.request
    if request.url != LISTING_URL or request.method != "POST":
        return False
    keys = {
        key for key, _ in parse_qsl(request.post_data or "", keep_blank_values=True)
    }
    return FORM_KEYS <= keys


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> None:
        # Never forward session credentials through an authentication redirect.
        return None


@dataclass(repr=False)
class ListingSource:
    """Runtime-only request template and captured page 1; never print or persist."""

    form: list[tuple[str, str]]
    headers: dict[str, str]
    first_page: ListingPage
    page_size: int
    finished_at: float
    first_duration: float | None
    used: bool = False
    received_at: datetime | None = None

    @classmethod
    def capture(cls, page: Page, response: Response) -> "ListingSource":
        try:
            if not listing_response(response) or response.status != 200:
                raise ValueError
            form = parse_qsl(response.request.post_data or "", keep_blank_values=True)
            values = dict(form)
            if len(values) != len(form) or values["page"] != "1":
                raise ValueError
            page_size = int(values["itemsPerPage"])
            if page_size <= 0:
                raise ValueError
            content_type = Message()
            content_type["content-type"] = response.headers.get("content-type", "")
            payload = ListingPage.model_validate_json(
                decode_response_text(
                    response.body(), content_type.get_content_charset()
                )
            )
            if payload.page != 1 or len(payload.data) > page_size:
                raise ValueError
            # Read cookies dynamically from the authenticated browser context.
            headers = {
                key: value
                for key, value in response.request.headers.items()
                if key.lower()
                in (
                    "content-type",
                    "accept",
                    "origin",
                    "referer",
                    "x-requested-with",
                    "user-agent",
                )
            }
            headers["Cookie"] = "; ".join(
                cookie["name"] + "=" + cookie["value"]
                for cookie in page.context.cookies(LISTING_URL)
            )
            timing = response.request.timing
            duration = timing["responseEnd"]
            return cls(
                form,
                headers,
                payload,
                page_size,
                time.monotonic()
                - max(0.0, time.time() - (timing["startTime"] + duration) / 1000)
                if duration >= 0
                else float("-inf"),
                duration / 1000 if duration >= 0 else None,
                received_at=datetime.fromtimestamp(
                    (timing["startTime"] + duration) / 1000, UTC
                )
                if duration >= 0
                else None,
            )
        except Exception:
            raise ListingFetchError(
                "Could not capture a valid page-1 listing request."
            ) from None

    def fetch(self, page_number: int) -> tuple[ListingPage, float]:
        """At most three attempts, waiting 2/4/8 seconds after the prior completion."""
        log = stage_logger("listings")
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return self._fetch_once(page_number, attempt_delay(attempt))
            except ListingFetchError as error:
                retry = error.retryable and attempt < MAX_ATTEMPTS
                log.warning(
                    "Page %d attempt %d/%d failed | category=%s | HTTP=%s | retry=%s",
                    page_number,
                    attempt,
                    MAX_ATTEMPTS,
                    error.category,
                    error.status if error.status is not None else "n/a",
                    retry,
                )
                if not retry:
                    raise
                log.info(
                    "Retrying the same page after %.0fs from failure completion.",
                    attempt_delay(attempt + 1),
                )
        raise AssertionError("Unreachable retry state")

    def _fetch_once(
        self, page_number: int, interval: float
    ) -> tuple[ListingPage, float]:
        time.sleep(remaining_delay(self.finished_at, time.monotonic(), interval))
        started = time.monotonic()
        try:
            body = urlencode(
                [
                    (key, str(page_number) if key == "page" else value)
                    for key, value in self.form
                ]
            ).encode("utf-8")
            request = Request(
                LISTING_URL, data=body, headers=self.headers, method="POST"
            )
            with build_opener(NoRedirect()).open(request, timeout=30) as response:
                if response.status != 200:
                    raise http_failure(response.status)
                declared_charset = response.headers.get_content_charset()
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                received_at = datetime.now(UTC)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ListingFetchError(
                    "Listing response exceeded the size limit.", FailureCategory.SIZE
                )
            payload = ListingPage.model_validate_json(
                decode_response_text(raw, declared_charset)
            )
            if payload.page != page_number or len(payload.data) > self.page_size:
                raise ListingFetchError(
                    "Listing pagination response did not match request."
                )
            self.received_at = received_at
            return payload, time.monotonic() - started
        except HTTPError as error:
            status = error.code
            error.close()
            raise http_failure(status) from None
        except ListingFetchError:
            raise
        except ValidationError as error:
            invalid_json = any(
                item["type"] == "json_invalid"
                for item in error.errors(
                    include_input=False, include_context=False, include_url=False
                )
            )
            category = FailureCategory.JSON if invalid_json else FailureCategory.SCHEMA
            raise ListingFetchError(
                "Listing response validation failed.", category
            ) from None
        except SSLError:
            raise ListingFetchError(
                "TLS validation or connection failed.", FailureCategory.TLS
            ) from None
        except TimeoutError:
            raise ListingFetchError(
                "Listing request timed out.", FailureCategory.TIMEOUT, retryable=True
            ) from None
        except URLError as error:
            category = (
                FailureCategory.TLS
                if isinstance(error.reason, SSLError)
                else FailureCategory.TIMEOUT
                if isinstance(error.reason, TimeoutError)
                else FailureCategory.NETWORK
            )
            raise ListingFetchError(
                "Listing transport failed.",
                category,
                retryable=category != FailureCategory.TLS,
            ) from None
        except (HTTPException, ConnectionError):
            raise ListingFetchError(
                "Listing response was interrupted.",
                FailureCategory.TRUNCATED,
                retryable=True,
            ) from None
        except Exception:
            raise ListingFetchError(
                "Listing fetch failed; diagnostics suppressed.",
                FailureCategory.INTERNAL,
            ) from None
        finally:
            # Only the interval after this fully consumed/failed response counts.
            self.finished_at = time.monotonic()


@dataclass(repr=False)
class ListingCollection:
    """Private in-memory results; assign before run() to retain partial progress."""

    source: ListingSource
    postings: dict[str, ListingRow] = field(default_factory=dict)
    pages_completed: int = 0
    records_received: int = 0
    duplicates: int = 0
    complete: bool = False
    received_at: dict[str, datetime] = field(default_factory=dict)

    def run(self) -> None:
        log = stage_logger("listings")
        if self.source.used:
            raise ListingFetchError(
                "Listing source already used; automatic restart disabled."
            )
        self.source.used = True
        if not capture_fresh(self.source.finished_at, time.monotonic()):
            raise ListingFetchError(
                "Listing capture is stale; start a fresh login.", FailureCategory.STALE
            )
        first = self.source.first_page
        total_pages = max(1, math.ceil(first.totalResults / self.source.page_size))
        durations: list[float] = []
        if self.source.first_duration is not None:
            durations.append(self.source.first_duration)
        try:
            for start in range(1, total_pages + 1, BATCH_SIZE):
                pages = batch_pages(start, total_pages)
                log.info(
                    "Batch %d/%d: pages %d-%d (sequential).",
                    (start - 1) // BATCH_SIZE + 1,
                    math.ceil(total_pages / BATCH_SIZE),
                    pages[0],
                    pages[-1],
                )
                for number in pages:
                    if number == 1:
                        payload = first  # Do not refetch the captured browser page.
                    else:
                        payload, duration = self.source.fetch(number)
                        durations.append(duration)
                    if payload.totalResults != first.totalResults:
                        log.warning(
                            "Listing total changed; retaining the initial page bound."
                        )
                    if not payload.data and first.totalResults > 0:
                        raise ListingFetchError(
                            "Unexpected empty listing page; partial results retained."
                        )
                    fresh = 0
                    for row in payload.data:
                        posting_id = row.posting_id()
                        if posting_id not in self.postings:
                            self.postings[posting_id] = row
                            if self.source.received_at is not None:
                                self.received_at[posting_id] = self.source.received_at
                            fresh += 1
                    self.records_received += len(payload.data)
                    self.duplicates += len(payload.data) - fresh
                    self.pages_completed += 1
                    average = sum(durations) / len(durations) if durations else None
                    log.info(
                        "Fetch finished: page %d/%d | received %d | records %d | "
                        "unique %d | duplicates %d | ETA %s%s",
                        number,
                        total_pages,
                        len(payload.data),
                        self.records_received,
                        len(self.postings),
                        self.duplicates,
                        eta_text(total_pages - number, average),
                        " (captured browser response, no replay)"
                        if number == 1
                        else "",
                    )
                    if number > 1 and payload.data and fresh == 0:
                        raise ListingFetchError(
                            "Page added no new IDs; stopped to avoid repeated fetching."
                        )
            self.complete = True
            log.info(
                "Listing pass complete: %d pages, %d unique postings. "
                "No detail requests.",
                self.pages_completed,
                len(self.postings),
            )
        except ListingFetchError as error:
            # Only our fixed, sanitized errors are logged here.
            log.error(
                "category=%s | %s Completed pages: %d; unique postings retained: %d.",
                error.category,
                str(error),
                self.pages_completed,
                len(self.postings),
            )
            raise
