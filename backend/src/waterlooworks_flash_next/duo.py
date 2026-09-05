"""Single sign-in submission and DOM-only Duo observation; never retries a push."""

import os
import re
import sys
import time
from typing import Never
from urllib.parse import urlsplit

from playwright.sync_api import Page

from waterlooworks_flash_next.logging import stage_logger
from waterlooworks_flash_next.login import (
    STEP_DELAY_MS,
    LoginSession,
    require_auth_host,
)

POLL_INTERVAL_MS = 500
CODE_POLL_ATTEMPTS = 10
DUO_HOST = "api-4ccc589b.duosecurity.com"
TRANSITION_TIMEOUT_SECONDS = 60


def authenticated_destination(url: str, dashboard: bool, logout: bool) -> bool:
    """Require both the verified origin and observed authenticated navigation."""
    target = urlsplit(url)
    return (
        target.scheme == "https"
        and target.hostname == "waterlooworks.uwaterloo.ca"
        and dashboard
        and logout
    )


def at_waterlooworks(page: Page) -> bool:
    if not authenticated_destination(page.url, True, True):
        return False
    dashboard = page.get_by_text("Dashboard", exact=False).first.is_visible()
    logout = any(
        page.get_by_text(label, exact=False).first.is_visible()
        for label in ("Logout", "Log out")
    )
    return authenticated_destination(page.url, dashboard, logout)


def on_duo(page: Page) -> bool:
    target = urlsplit(page.url)
    return target.scheme == "https" and target.hostname == DUO_HOST


def stop_login(session: LoginSession, message: str) -> Never:
    """Log a fixed safe reason, close after three seconds, and exit even in -i mode."""
    log = stage_logger("duo")
    log.error("%s Closing browser in 3 seconds; exit code 1.", message)
    time.sleep(3)
    try:
        session.close()
    except Exception:
        log.error("Browser cleanup failed; sensitive diagnostics suppressed.")
    finally:
        # SystemExit in a startup script can leave `python -i` at its prompt.
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        finally:
            os._exit(1)


def check_duo_failure(session: LoginSession) -> None:
    """Handle observed failures via existing DOM text; never retry authentication."""
    if not on_duo(session.page):
        return
    if session.page.get_by_text(
        "Invalid verification code", exact=False
    ).first.is_visible():
        stop_login(session, "Duo rejected the verification code.")
    if any(
        session.page.get_by_text(label, exact=False).first.is_visible()
        for label in ("Request timed out", "Push timed out")
    ):
        stop_login(session, "Duo request timed out.")


def visible_code(page: Page) -> str | None:
    """Conservative DOM candidate matching, successful in the guided live run.

    Require the observed code prompt and exactly one visible three-digit text node.
    Never read input values, phone labels, page dumps, or authentication URLs.
    """
    if not on_duo(page):
        return None
    if not any(
        page.get_by_text(label, exact=False).first.is_visible()
        for label in ("verification code", "code in Duo Mobile")
    ):
        return None
    candidates = page.get_by_text(re.compile(r"^\s*[0-9]{3}\s*$"))
    visible = [candidate for candidate in candidates.all() if candidate.is_visible()]
    if len(visible) != 1:
        return None
    code = visible[0].inner_text().strip()
    return code if re.fullmatch(r"[0-9]{3}", code) else None


def accept_device_if_prompted(page: Page) -> bool:
    """Accept the optional, user-approved device trust prompt only when visible."""
    if not on_duo(page):
        return False
    if not page.get_by_text("Is this your device?", exact=True).first.is_visible():
        return False
    button = page.get_by_role("button", name="Yes, this is my device", exact=True)
    if not button.is_visible():
        return False
    button.click()
    stage_logger("duo").info(
        "Accepted optional device trust prompt; waiting for WaterlooWorks."
    )
    return True


def observe_duo(session: LoginSession) -> None:
    """Observe both challenged and trusted-device redirects without extra requests.

    Logging the MFA code is explicitly user-authorized. Run in a private console;
    do not enable log collection or share output. No credentials-file values log.
    """
    page = session.page
    log = stage_logger("duo")
    attempts = 0
    code_logged = False
    trust_accepted = False
    deadline = time.monotonic() + TRANSITION_TIMEOUT_SECONDS
    while True:
        page.wait_for_timeout(POLL_INTERVAL_MS)
        if at_waterlooworks(page):
            log.info("WaterlooWorks reached: Dashboard and logout controls visible.")
            return
        target = urlsplit(page.url)
        if target.scheme != "https" or target.hostname not in (
            DUO_HOST,
            "adfs.uwaterloo.ca",
            "waterlooworks.uwaterloo.ca",
        ):
            log.warning(
                "Unrecognized authentication destination; pausing for guidance."
            )
            return
        check_duo_failure(session)
        if on_duo(page):
            if not trust_accepted and accept_device_if_prompted(page):
                trust_accepted = True
                deadline = time.monotonic() + TRANSITION_TIMEOUT_SECONDS
            elif not trust_accepted:
                code = visible_code(page)
                if code is not None:
                    if not code_logged:
                        log.info("Detected verification code: [%s] (waiting...)", code)
                        code_logged = True
                    del code
                    # The visible challenge is still awaiting approval. Its own
                    # timeout UI controls expiry; don't time out a user mid-approval.
                    deadline = time.monotonic() + TRANSITION_TIMEOUT_SECONDS
                elif not code_logged:
                    attempts += 1
                    log.debug(
                        "Checking existing DOM for code (%d/%d).",
                        attempts,
                        CODE_POLL_ATTEMPTS,
                    )
                    if attempts >= CODE_POLL_ATTEMPTS:
                        stop_login(
                            session,
                            "Verification code not detected after 10 DOM checks.",
                        )
        # A brief Duo hop may never be observed; WaterlooWorks is a valid direct
        # destination. ADFS/DOM transitions are not automatically a failure.
        if time.monotonic() >= deadline:
            stop_login(session, "Authentication transition exceeded 60 seconds.")


def submit_and_observe_duo(session: LoginSession) -> None:
    """Submit once and observe either Duo or the authenticated WaterlooWorks page."""
    log = stage_logger("duo")
    try:
        require_auth_host(session.page.url)
        session.page.wait_for_timeout(STEP_DELAY_MS)
        log.info("Starting stage: sign in (single submission).")
        session.page.get_by_role("button", name="Sign in", exact=True).click()
        log.info(
            "Completed stage: sign in. Observing Duo or WaterlooWorks via DOM only."
        )
        observe_duo(session)
    except Exception:
        stop_login(
            session, "Duo observation stopped; sensitive diagnostics suppressed."
        )
