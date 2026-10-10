"""Partial listing persistence, request-derived capture waits and analysis scope.

Primitive clocks, gates and empty control runs only; no posting records, markup
or platform/provider responses are fabricated.
"""

import ast
import inspect
import textwrap
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from playwright.sync_api import Page, Route

from waterlooworks_flash_next.database import open_database, start_collection_run
from waterlooworks_flash_next.detail_capture import (
    CAPTURE_IDLE_SECONDS,
    CaptureGate,
    install_capture_guard,
    wait_for_capture,
)
from waterlooworks_flash_next.listings import (
    LISTING_URL,
    ListingCollection,
    ListingPage,
    ListingSource,
)
from waterlooworks_flash_next.refresh import RefreshService, analysis_batches


def _empty_collection() -> ListingCollection:
    source = ListingSource(
        form=[],
        headers={},
        first_page=ListingPage.model_validate(
            {"totalResults": 0, "page": 1, "data": []}
        ),
        page_size=50,
        finished_at=time.monotonic(),
        first_duration=None,
    )
    return ListingCollection(source)


def test_failed_or_interrupted_listing_pass_is_saved_before_reraising() -> None:
    tree = ast.parse(textwrap.dedent(inspect.getsource(RefreshService._run)))
    handler = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Try)
        and any(ast.unparse(stmt) == "collection.run()" for stmt in node.body)
    )
    assert handler.handlers[0].type is not None
    caught = ast.unparse(handler.handlers[0].type)
    assert "ListingFetchError" in caught and "CollectionInterrupted" in caught
    body = [ast.unparse(stmt) for stmt in handler.handlers[0].body]
    assert "_save_partial_listings" in body[0]
    assert body[-1] == "raise"
    # A request cap is a clean partial finish: saved, never re-raised as failure.
    capped = handler.handlers[1]
    assert capped.type is not None
    assert ast.unparse(capped.type) == "RequestBudgetExhausted"
    capped_body = [ast.unparse(stmt) for stmt in capped.body]
    assert "_save_partial_listings" in capped_body[0]
    assert "success, category = (True, None)" in capped_body
    assert "raise" not in capped_body


def test_partial_save_reports_what_was_saved(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    connection = open_database(path)
    run_id = start_collection_run(connection)
    connection.close()
    service = RefreshService(path, None)
    service._save_partial_listings(run_id, _empty_collection())
    messages = [log.message for log in service.snapshot().logs]
    assert messages == [
        "Partial listing pass saved: 0 pages, 0 unique postings. "
        "No deletion is inferred; details and analysis were not run."
    ]


def test_partial_save_failure_is_reported_without_masking_the_original(
    tmp_path: Path,
) -> None:
    path = tmp_path / "db.sqlite3"
    open_database(path).close()
    service = RefreshService(path, None)
    # Unknown run: persistence refuses, and the helper must not raise.
    service._save_partial_listings("missing-run", _empty_collection())
    logs = service.snapshot().logs
    assert [(log.level, log.message) for log in logs] == [
        (
            "error",
            "Partial listing pass could not be saved; diagnostics suppressed.",
        )
    ]


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_capture_wait_extends_past_slow_paced_request() -> None:
    clock = Clock()
    gate = CaptureGate("control")
    ticks: list[float] = []

    def wait_for_timeout(milliseconds: float) -> None:
        clock.now += milliseconds / 1000
        ticks.append(clock.now)
        if len(ticks) == 1:
            # A metadata request whose paced retries take 40 s, then settles.
            clock.now += 40
            gate.settled_at = clock.now
        elif clock.now >= 45 and not gate.complete:
            gate.complete = True

    page = cast(Page, SimpleNamespace(wait_for_timeout=wait_for_timeout))
    wait_for_capture(page, gate, clock=clock)
    assert gate.complete
    assert 45 <= clock.now < 40 + CAPTURE_IDLE_SECONDS + 1


def test_capture_wait_still_bounds_an_idle_page() -> None:
    clock = Clock()
    gate = CaptureGate("control")

    def wait_for_timeout(milliseconds: float) -> None:
        clock.now += milliseconds / 1000

    page = cast(Page, SimpleNamespace(wait_for_timeout=wait_for_timeout))
    wait_for_capture(page, gate, clock=clock)
    assert not gate.complete
    assert CAPTURE_IDLE_SECONDS <= clock.now < CAPTURE_IDLE_SECONDS + 0.2


def test_guard_records_when_an_owned_request_settles() -> None:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    handlers: list[Callable[[Route], None]] = []
    frame = object()
    page = cast(
        Page,
        SimpleNamespace(
            main_frame=frame,
            context=SimpleNamespace(
                route=lambda pattern, handler: handlers.append(handler)
            ),
        ),
    )

    def fetch(**kwargs: object) -> None:
        # Transport control failure only; no fabricated response body.
        raise PlaywrightTimeoutError("private transport diagnostics")

    gate = install_capture_guard(
        page, "1", request=lambda operation: operation(), consume=lambda t, g: None
    )
    route = cast(
        Route,
        SimpleNamespace(
            request=SimpleNamespace(
                url=LISTING_URL,
                resource_type="xhr",
                method="POST",
                post_data="postingId=1&action=control",
                frame=frame,
            ),
            fetch=fetch,
            abort=lambda: None,
        ),
    )
    before = time.monotonic()
    handlers[0](route)
    assert gate.settled_at >= before


def test_analysis_batches_never_exceed_job_bound() -> None:
    from waterlooworks_flash_next.inference import MAX_JOB_POSTINGS

    ids = [str(index) for index in range(MAX_JOB_POSTINGS * 2 + 3)]
    batches = analysis_batches(ids)
    assert [len(batch) for batch in batches] == [MAX_JOB_POSTINGS] * 2 + [3]
    assert [item for batch in batches for item in batch] == ids
    assert analysis_batches([]) == []


def test_analysis_selects_only_postings_observed_in_this_run() -> None:
    source = inspect.getsource(RefreshService._analyze_saved_data)
    assert "posting_observations" in source
    assert "FROM posting_contents ORDER BY" not in source
