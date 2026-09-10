"""Single sign-in submission and DOM-only Duo observation; never retries a push."""

import re
from collections.abc import Callable
from typing import Never

from playwright.sync_api import Page

from waterlooworks_flash_next.logging import stage_logger
from waterlooworks_flash_next.login import (
    STEP_DELAY_MS,
    LoginFlowError,
    LoginSession,
    exact_auth_origin,
    require_auth_host,
)

POLL_INTERVAL_MS = 500
CODE_POLL_ATTEMPTS = 10
DUO_HOST = "api-4ccc589b.duosecurity.com"


def authenticated_destination(url: str, dashboard: bool, logout: bool) -> bool:
    """Require both the verified origin and observed authenticated navigation."""
    return exact_auth_origin(url, "waterlooworks.uwaterloo.ca") and dashboard and logout


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
    return exact_auth_origin(page.url, DUO_HOST)


def stop_login(session: LoginSession, message: str) -> Never:
    """Raise safely after cleanup; never terminate the API process."""
    stage_logger("duo").error("Authentication stopped; no automatic retry.")
    try:
        session.close()
    except Exception:
        stage_logger("duo").error(
            "Browser cleanup failed; sensitive diagnostics suppressed."
        )
    raise LoginFlowError(message) from None


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


def observe_duo(
    session: LoginSession,
    on_code: Callable[[str], None] | None = None,
    clear_code: Callable[[], None] | None = None,
) -> None:
    """Observe only verified DOM branches within the original overall deadline."""
    page = session.page
    log = stage_logger("duo")
    attempts = 0
    code_seen = False
    trust_accepted = False
    while True:
        session.deadline.bound_page(page)
        page.wait_for_timeout(min(POLL_INTERVAL_MS, session.deadline.remaining_ms()))
        session.deadline.bound_page(page)
        if at_waterlooworks(page):
            session.deadline.remaining_ms()
            page.set_default_timeout(30_000)
            page.set_default_navigation_timeout(60_000)
            log.info("WaterlooWorks reached: Dashboard and logout controls visible.")
            return
        if not any(
            exact_auth_origin(page.url, host)
            for host in (
                DUO_HOST,
                "adfs.uwaterloo.ca",
                "waterlooworks.uwaterloo.ca",
            )
        ):
            stop_login(session, "Unrecognized authentication destination.")
        check_duo_failure(session)
        if on_duo(page):
            if not trust_accepted and accept_device_if_prompted(page):
                trust_accepted = True
                if clear_code:
                    clear_code()
            elif not trust_accepted:
                code = visible_code(page)
                if code is not None:
                    if not code_seen:
                        log.info(
                            "Duo approval required; open Admin settings "
                            "from the account button."
                        )
                        code_seen = True
                    if on_code:
                        on_code(code)
                    del code
                elif not code_seen:
                    attempts += 1
                    if attempts >= CODE_POLL_ATTEMPTS:
                        stop_login(
                            session,
                            "Verification code not detected after 10 DOM checks.",
                        )
        session.deadline.remaining_ms()


def submit_and_observe_duo(
    session: LoginSession,
    on_code: Callable[[str], None] | None = None,
    clear_code: Callable[[], None] | None = None,
) -> None:
    """Submit at most once, including after an ambiguous click failure."""
    log = stage_logger("duo")
    try:
        if session.submitted:
            raise LoginFlowError("Sign-in was already submitted; no retry.")
        require_auth_host(session.page.url)
        session.deadline.bound_page(session.page)
        session.page.wait_for_timeout(
            min(STEP_DELAY_MS, session.deadline.remaining_ms())
        )
        session.deadline.bound_page(session.page)
        require_auth_host(session.page.url)
        session.claim_submission()
        log.info("Starting stage: sign in (single submission).")
        session.page.get_by_role("button", name="Sign in", exact=True).click()
        log.info(
            "Completed stage: sign in. Observing Duo or WaterlooWorks via DOM only."
        )
        observe_duo(session, on_code, clear_code)
    except Exception:
        stop_login(session, "Authentication stopped; sensitive diagnostics suppressed.")
    finally:
        if clear_code:
            clear_code()
