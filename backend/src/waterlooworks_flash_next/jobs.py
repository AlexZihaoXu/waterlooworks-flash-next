"""Verified job-board navigation and passive listing-response capture."""

import re
import time
from urllib.parse import urlsplit

from playwright.sync_api import Page, Request, Response

from waterlooworks_flash_next.duo import at_waterlooworks
from waterlooworks_flash_next.listings import ListingSource, listing_response
from waterlooworks_flash_next.logging import stage_logger
from waterlooworks_flash_next.login import STEP_DELAY_MS

JOB_BOARD_URL = "https://waterlooworks.uwaterloo.ca/myAccount/co-op/full/jobs.htm"


def open_all_jobs(page: Page) -> ListingSource | None:
    """Capture from before navigation; All Jobs need not trigger another request."""
    log = stage_logger("jobs")
    stage = "confirm dashboard"
    captured: list[Response] = []
    pending: set[Request] = set()
    last_finished = time.monotonic()

    def started(request: Request) -> None:
        if urlsplit(
            request.url
        ).hostname == "waterlooworks.uwaterloo.ca" and request.resource_type in (
            "xhr",
            "fetch",
        ):
            pending.add(request)

    def finished(request: Request) -> None:
        nonlocal last_finished
        if request in pending:
            pending.discard(request)
            last_finished = time.monotonic()

    def response_received(response: Response) -> None:
        if listing_response(response):
            captured.append(response)

    # Navigation itself loads the listing. Installing listeners after the click
    # misses that response when All Jobs is already the current runtime selection.
    page.on("request", started)
    page.on("requestfinished", finished)
    page.on("requestfailed", finished)
    page.on("response", response_received)
    try:
        if not at_waterlooworks(page):
            log.warning("Dashboard not confirmed; skipping job-board navigation.")
            return None
        page.wait_for_timeout(STEP_DELAY_MS)
        stage = "open board"
        log.info("Starting stage: open Full Cycle Service Job Board; capture active.")
        page.goto(JOB_BOARD_URL, wait_until="load")
        log.info("Completed stage: job-board page loaded.")
        page.wait_for_timeout(STEP_DELAY_MS)
        stage = "locate All Jobs button"
        button = page.get_by_role("button", name=re.compile(r"^all jobs$", re.I))
        button.wait_for(state="visible")
        stage = "click All Jobs"
        log.info("Starting stage: click All Jobs once.")
        before_click = len(captured)
        button.click()
        log.info("All Jobs click completed; checking captured listing responses.")
        stage = "wait for existing listing response to finish"
        deadline = time.monotonic() + 30
        last_finished = max(last_finished, time.monotonic())
        while not captured or pending or time.monotonic() - last_finished < 0.25:
            if time.monotonic() >= deadline:
                log.error(
                    "Listing did not settle within 30s; no replay or extra click."
                )
                return None
            page.wait_for_timeout(100)
        stage = "validate latest captured response"
        source = ListingSource.capture(page, captured[-1])
        log.info(
            "Page 1 captured | responses=%d | after-click responses=%d.",
            len(captured),
            len(captured) - before_click,
        )
        return source
    except Exception as error:
        log.error(
            "Board stopped | stage=%s | error_type=%s; no retry.",
            stage,
            type(error).__name__,
        )
        return None
    finally:
        page.remove_listener("request", started)
        page.remove_listener("requestfinished", finished)
        page.remove_listener("requestfailed", finished)
        page.remove_listener("response", response_received)
