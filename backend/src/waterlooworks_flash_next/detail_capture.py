"""Bounded real title bootstrap, invoked only after the full listing commit."""

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from email.message import Message
from enum import StrEnum
from urllib.parse import parse_qsl

from playwright.sync_api import Page, Route
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from waterlooworks_flash_next.content import extract_content
from waterlooworks_flash_next.database import StoredListing
from waterlooworks_flash_next.detail_fetch import (
    MAX_DETAIL_BYTES,
    DetailFetchError,
    DetailSource,
    retry_after_seconds,
    unexpected_detail_body,
)
from waterlooworks_flash_next.listings import (
    LISTING_URL,
    CollectionInterrupted,
    FailureCategory,
    ListingSource,
    decode_response_text,
    http_failure,
)
from waterlooworks_flash_next.ww_pacing import RequestBudgetExhausted


class CaptureStage(StrEnum):
    SELECT = "select_listing"
    TITLES = "match_titles"
    INSTALL = "install_guard"
    CLICK = "click_title"
    WAIT = "wait_templates"
    SESSION = "copy_session"
    COMPLETE = "complete"
    GUARD = "guard_event"


class CaptureReason(StrEnum):
    NONE = "none"
    DESTINATION = "destination"
    METHOD = "method"
    FRAME = "frame"
    RESOURCE = "resource"
    FORM = "form"
    DUPLICATE = "duplicate"
    EXCESS = "excess_templates"
    INSPECTION = "request_inspection"
    TITLES = "title_count"
    LISTING = "eligible_listing"
    COUNT = "template_count"
    EXCEPTION = "capture_exception"


class ResourceKind(StrEnum):
    IMAGE = "image"
    FONT = "font"
    STYLESHEET = "stylesheet"
    MEDIA = "media"
    TEXTTRACK = "texttrack"
    SCRIPT = "script"
    DOCUMENT = "document"
    XHR = "xhr"
    FETCH = "fetch"
    OTHER = "other"
    UNKNOWN = "unknown"


class MethodClass(StrEnum):
    GET = "get"
    POST = "post"
    OTHER = "other"
    UNKNOWN = "unknown"


class CapturePhase(StrEnum):
    IDLE = "idle"
    ACTIVE = "active"
    COMPLETED = "completed"


class BlockDisposition(StrEnum):
    DISCARDED = "discarded"
    FATAL = "fatal"


# Page-side idle window: how long the page may take to dispatch the next title
# read. Time spent inside the shared pacing gate (3-6 s gap, 30 s fetch timeout,
# one 30 s retry; no rest during bootstrap) never consumes it; each settled
# request restarts it.
CAPTURE_IDLE_SECONDS = 10.0

DISCARDED_RESOURCES = frozenset(
    {"image", "font", "stylesheet", "media", "texttrack", "script"}
)


def blocked_diagnostic(
    kind: ResourceKind,
    method: MethodClass,
    phase: CapturePhase,
    disposition: BlockDisposition,
    count: int,
) -> str:
    """Fixed classes only; count saturates at 100000 per class combination."""
    if (
        not isinstance(kind, ResourceKind)
        or not isinstance(method, MethodClass)
        or not isinstance(phase, CapturePhase)
        or not isinstance(disposition, BlockDisposition)
        or type(count) is not int
        or not 0 <= count <= 100000
    ):
        return "Detail capture diagnostic: invalid diagnostic withheld."
    return (
        f"Detail capture blocked: resource={kind.value}; method={method.value}; "
        f"phase={phase.value}; disposition={disposition.value}; count={count}."
    )


def capture_diagnostic(
    stage: CaptureStage,
    titles: int,
    templates: int,
    reason: CaptureReason,
    unexpected: int,
) -> str:
    """Only enums and bounded integer counters enter SSE; no source values."""
    if (
        not isinstance(stage, CaptureStage)
        or not isinstance(reason, CaptureReason)
        or any(
            type(n) is not int or not 0 <= n <= 100000
            for n in (titles, templates, unexpected)
        )
    ):
        return "Detail capture diagnostic: invalid diagnostic withheld."
    return (
        f"Detail capture: stage={stage.value}; title_matches={titles}; "
        f"templates={templates}; reason={reason.value}; unexpected={unexpected}."
    )


@dataclass(repr=False)
class CaptureGate:
    """Allow only bounded, distinct original-frame title-read forms."""

    identity: str
    templates: list[tuple[tuple[str, str], ...]] = field(default_factory=list)
    invalid: bool = False
    reason: CaptureReason = CaptureReason.NONE
    unexpected: int = 0
    blocked: dict[
        tuple[ResourceKind, MethodClass, CapturePhase, BlockDisposition], int
    ] = field(default_factory=dict)
    active: bool = False
    complete: bool = False
    settled_at: float = 0.0
    error: DetailFetchError | None = None
    # Clean pacing stop (cap or shutdown) raised inside the route handler.
    stop: RequestBudgetExhausted | CollectionInterrupted | None = None
    report: Callable[["CaptureGate"], None] | None = field(default=None, repr=False)

    def reject(self, reason: CaptureReason) -> None:
        if not self.invalid:
            self.reason = reason
        self.invalid = True
        self.unexpected = min(self.unexpected + 1, 100000)

    def observe(
        self, url: str, method: str, body: str, *, main_frame: bool, resource_type: str
    ) -> bool:
        if method == "GET" and resource_type in DISCARDED_RESOURCES:
            return False
        form = tuple(parse_qsl(body, keep_blank_values=True))
        values = dict(form)
        reason = (
            CaptureReason.EXCESS
            if self.invalid or self.active or self.complete
            else CaptureReason.DESTINATION
            if url != LISTING_URL
            else CaptureReason.METHOD
            if method != "POST"
            else CaptureReason.FRAME
            if not main_frame
            else CaptureReason.RESOURCE
            if resource_type not in ("xhr", "fetch")
            else CaptureReason.FORM
            if len(form) != len(values)
            or set(values) != {"action", "postingId"}
            or values.get("postingId") != self.identity
            or not values.get("action")
            else CaptureReason.EXCESS
            if len(self.templates) >= 2
            else CaptureReason.DUPLICATE
            if form in self.templates
            else CaptureReason.NONE
        )
        if reason != CaptureReason.NONE:
            self.reject(reason)
        else:
            self.templates.append(form)
            return True
        return False


def forward_bootstrap_metadata(
    content_ready: bool, forward: Callable[[], None]
) -> None:
    """Only metadata must reach the page to dispatch its dependent markup read.

    Keep the final markup request paused until browser closure: rendering it or
    aborting it early can dispatch page resources/error-handler requests.
    """
    if not content_ready:
        forward()


def install_capture_guard(
    page: Page,
    identity: str,
    report: Callable[[CaptureGate], None] | None = None,
    *,
    request: Callable[[Callable[[], str]], str] | None = None,
    consume: Callable[[str, CaptureGate], None] | None = None,
    blocked_report: Callable[[str], None] | None = None,
) -> CaptureGate:
    """Context-wide containment; only validated bootstrap forms reach the network.

    The controller records intent durably before route.fetch, shares the direct
    collector's pacing budget, and disables hidden redirects/retries.
    """
    gate = CaptureGate(identity, report=report)

    def intercept(route: Route) -> None:
        fulfilled = False
        owns_active = False
        accepted = False
        discarded = False
        kind = ResourceKind.UNKNOWN
        method_class = MethodClass.UNKNOWN
        phase = (
            CapturePhase.COMPLETED
            if gate.complete
            else CapturePhase.ACTIVE
            if gate.active
            else CapturePhase.IDLE
        )
        try:
            incoming = route.request
            resource_type = incoming.resource_type
            kind = next(
                (k for k in ResourceKind if k.value == resource_type),
                ResourceKind.OTHER,
            )
            method = incoming.method
            method_class = (
                MethodClass.GET
                if method == "GET"
                else MethodClass.POST
                if method == "POST"
                else MethodClass.OTHER
            )
            if method == "GET" and resource_type in DISCARDED_RESOURCES:
                discarded = True
                return
            accepted = gate.observe(
                incoming.url,
                method,
                incoming.post_data or "",
                main_frame=incoming.frame == page.main_frame,
                resource_type=resource_type,
            )
            if not accepted or gate.invalid or request is None or consume is None:
                return
            gate.active = True
            owns_active = True

            def operation() -> str:
                nonlocal fulfilled
                if gate.invalid:
                    raise DetailFetchError(FailureCategory.SCHEMA)
                try:
                    response = route.fetch(
                        max_redirects=0, max_retries=0, timeout=30000
                    )
                except PlaywrightTimeoutError:
                    raise DetailFetchError(
                        FailureCategory.TIMEOUT, retryable=True
                    ) from None
                try:
                    status = response.status
                    if status != 200:
                        failure = http_failure(
                            status,
                            retry_after_seconds(
                                response.headers.get("retry-after"), now=time.time()
                            ),
                            response.headers.get("location"),
                        )
                        raise DetailFetchError(
                            failure.category,
                            status=status,
                            retryable=failure.retryable,
                            retry_after=failure.retry_after,
                        )
                    if len(response.body()) > MAX_DETAIL_BYTES:
                        raise DetailFetchError(FailureCategory.SIZE)
                    encoding = Message()
                    encoding["content-type"] = response.headers.get("content-type", "")
                    text = decode_response_text(
                        response.body(), encoding.get_content_charset()
                    )
                    try:
                        consume(text, gate)
                    except Exception:
                        raise unexpected_detail_body(text) or DetailFetchError(
                            FailureCategory.SCHEMA
                        ) from None
                    if gate.invalid:
                        raise DetailFetchError(FailureCategory.SCHEMA)
                    forward_bootstrap_metadata(
                        gate.complete, lambda: route.fulfill(response=response)
                    )
                    fulfilled = not gate.complete
                    return text
                finally:
                    response.dispose()

            request(operation)
        except DetailFetchError as error:
            gate.error = error
            gate.reject(CaptureReason.EXCEPTION)
        except (RequestBudgetExhausted, CollectionInterrupted) as stop:
            gate.stop = stop
            gate.reject(CaptureReason.EXCEPTION)
        except Exception:
            gate.reject(CaptureReason.INSPECTION)
        finally:
            if owns_active:
                gate.active = False
                gate.settled_at = time.monotonic()
            try:
                if (
                    discarded
                    or not accepted
                    or (not fulfilled and (not gate.complete or gate.invalid))
                ):
                    route.abort()
            except Exception:
                discarded = False
                gate.reject(CaptureReason.INSPECTION)
            finally:
                if discarded or gate.invalid:
                    disposition = (
                        BlockDisposition.DISCARDED
                        if discarded
                        else BlockDisposition.FATAL
                    )
                    key = (kind, method_class, phase, disposition)
                    gate.blocked[key] = min(gate.blocked.get(key, 0) + 1, 100000)
                    if blocked_report:
                        blocked_report(blocked_diagnostic(*key, gate.blocked[key]))
                if gate.report:
                    gate.report(gate)

    page.context.route("**/*", intercept)
    return gate


def wait_for_capture(
    page: Page, gate: CaptureGate, *, clock: Callable[[], float] = time.monotonic
) -> None:
    """Wait until the gate completes or fails, or the page stays idle too long.

    The deadline is derived from the request controller's own timing: it is
    measured from the later of the click and the last settled bootstrap request,
    so a slow but successful paced metadata request still lets the page
    dispatch its dependent markup read. At most two requests are accepted, so
    the total wait stays bounded.
    """
    started = clock()
    while clock() < max(started, gate.settled_at) + CAPTURE_IDLE_SECONDS:
        page.wait_for_timeout(100)
        if gate.invalid or gate.complete:
            break


def capture_detail_source(
    page: Page,
    listing: ListingSource,
    posting: StoredListing,
    request: Callable[[Callable[[], str]], str],
    diagnostic: Callable[[str], None] | None = None,
) -> DetailSource:
    """Reuse actual sanitized first content; leave the guard installed until close."""
    source = DetailSource((), {}, (), capture_check=lambda: False)

    def consume(text: str, current: CaptureGate) -> None:
        try:
            metadata = json.loads(text)
        except ValueError:
            metadata = None
        else:
            if (
                not isinstance(metadata, dict)
                or str(metadata.get("id")) != posting.id
                or not {"position", "org", "div", "divId"} <= metadata.keys()
                or len(current.templates) != 1
            ):
                raise DetailFetchError(FailureCategory.SCHEMA)
            return
        cookies = page.context.cookies(LISTING_URL)
        secrets = tuple(cookie["value"] for cookie in cookies) + tuple(
            value
            for form in current.templates
            for key, value in form
            if key != "postingId"
        )
        source.first_content = extract_content(
            text, posting.id, posting.title or "", posting.organization, secrets=secrets
        )
        source.markup_index = len(current.templates) - 1
        current.complete = True

    stage = CaptureStage.SELECT
    reason = CaptureReason.NONE
    title_matches = 0
    gate: CaptureGate | None = None

    def emit(current: CaptureStage) -> None:
        if diagnostic:
            diagnostic(
                capture_diagnostic(
                    current,
                    title_matches,
                    len(gate.templates) if gate else 0,
                    gate.reason if gate and gate.invalid else reason,
                    gate.unexpected if gate else 0,
                )
            )

    emit(stage)
    try:
        identity, title = posting.id, posting.title
        if not re.fullmatch(r"[0-9]+", identity) or not title:
            reason = CaptureReason.LISTING
            raise DetailFetchError(FailureCategory.SCHEMA)
        stage = CaptureStage.TITLES
        emit(stage)
        titles = page.get_by_text(title, exact=True)
        deadline = time.monotonic() + 10
        while not titles.count() and time.monotonic() < deadline:
            page.wait_for_timeout(100)
        matches = titles.evaluate_all(
            """(nodes,id)=>nodes.map(n=>{
            if(!n.getClientRects().length ||
                getComputedStyle(n).visibility==='hidden')return false;
            for(let p=n,i=0;p && i<7;p=p.parentElement,i++){
                const text=p.innerText || '';
                const ownsId=new RegExp('(^|[^0-9])'+id+'([^0-9]|$)').test(text);
                if(text.length<2000 && ownsId)return true;
            }
            return false;
        })""",
            identity,
        )
        candidates = [index for index, value in enumerate(matches) if value is True]
        title_matches = len(candidates)
        emit(stage)
        if len(candidates) != 1:
            reason = CaptureReason.TITLES
            raise DetailFetchError(FailureCategory.SCHEMA)
        stage = CaptureStage.INSTALL
        emit(stage)
        gate = install_capture_guard(
            page,
            identity,
            lambda g: (
                diagnostic(
                    capture_diagnostic(
                        CaptureStage.GUARD,
                        title_matches,
                        len(g.templates),
                        g.reason,
                        g.unexpected,
                    )
                )
                if diagnostic
                else None
            ),
            request=request,
            consume=consume,
            blocked_report=diagnostic,
        )
        # Keep this deny route until browser closure, including on failure.
        # Unroute would let queued UI retries escape before session.close().
        stage = CaptureStage.CLICK
        emit(stage)
        titles.nth(candidates[0]).click()
        stage = CaptureStage.WAIT
        emit(stage)
        wait_for_capture(page, gate)
        templates = gate.templates
        if gate.stop:
            raise gate.stop
        if gate.error:
            raise gate.error
        if gate.invalid or not gate.complete:
            reason = CaptureReason.COUNT
            raise DetailFetchError(FailureCategory.SCHEMA)
        stage = CaptureStage.SESSION
        emit(stage)
        cookies = page.context.cookies(LISTING_URL)
        headers = {
            key: value
            for key, value in listing.headers.items()
            if key.lower() != "cookie"
        }
        headers["Cookie"] = "; ".join(
            cookie["name"] + "=" + cookie["value"] for cookie in cookies
        )
        headers["Accept-Encoding"] = "identity"
        secrets = tuple(cookie["value"] for cookie in cookies) + tuple(
            value for form in templates for key, value in form if key != "postingId"
        )
        stage = CaptureStage.COMPLETE
        emit(stage)
        source.templates = tuple(templates)
        source.headers = headers
        source.secrets = secrets
        source.capture_check = lambda: not gate.invalid
        return source
    except (DetailFetchError, RequestBudgetExhausted, CollectionInterrupted):
        emit(stage)
        raise
    except Exception:
        if gate and gate.stop:
            emit(stage)
            raise gate.stop from None
        if gate and gate.error:
            emit(stage)
            raise gate.error from None
        if reason == CaptureReason.NONE:
            reason = CaptureReason.EXCEPTION
        emit(stage)
        raise DetailFetchError(FailureCategory.SCHEMA) from None
