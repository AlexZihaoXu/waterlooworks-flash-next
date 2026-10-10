"""Explicit bounded login, headless by default, stopping before password submission.

Never enable Playwright debug logging, tracing, or screenshots for this flow.
"""

import os
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from playwright.sync_api import (
    BrowserContext,
    Page,
    Playwright,
    sync_playwright,
)

from waterlooworks_flash_next.credentials import CredentialStore
from waterlooworks_flash_next.logging import stage_logger
from waterlooworks_flash_next.runtime_secrets import (
    load_local_environment,
    runtime_value,
)

STEP_DELAY_MS = 250
TYPING_DELAY_MS = 60_000 / (120 * 1.5 * 2 * 5)  # 360 words/min, 5 characters/word.
STEPS = (
    "start window",
    "1024x768",
    "WaterlooWorks",
    "wait for landing page load",
    "student login",
    "wait for authentication page load",
    "enter username",
    "next",
    "enter password",
    "password ready",
)
USERNAME_SELECTOR = (
    "input:visible:not([type=hidden]):not([type=password])"
    ":not([type=submit]):not([type=button]):not([type=checkbox])"
)


class LoginFlowError(Exception):
    """A deliberately sanitized login error."""


class AuthDeadline:
    """Fixed monotonic deadline shared by credential entry and Duo observation."""

    def __init__(
        self, *, seconds: float = 180, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._clock = clock
        self.expires_at = clock() + seconds

    def remaining_ms(self) -> float:
        remaining = self.expires_at - self._clock()
        if remaining <= 0:
            raise LoginFlowError("Authentication deadline exceeded.")
        return remaining * 1000

    def bound_page(self, page: Page) -> None:
        remaining = self.remaining_ms()
        page.set_default_timeout(min(30_000, remaining))
        page.set_default_navigation_timeout(min(60_000, remaining))


@dataclass(frozen=True)
class BrowserSettings:
    headless: bool
    profile: Path


def browser_settings() -> BrowserSettings:
    """Explicit private profile; headed mode is an opt-in local operator setting."""
    configured_mode = runtime_value("WW_BROWSER_HEADLESS")
    mode = "1" if configured_mode is None else configured_mode
    profile = runtime_value("WW_BROWSER_PROFILE")
    if mode not in {"0", "1"} or not profile or not Path(profile).is_absolute():
        raise LoginFlowError(
            "Configure browser mode and an absolute private profile path."
        )
    return BrowserSettings(mode == "1", Path(profile))


_BROWSER_DENIED_PREFIXES = ("WW_", "FIREBASE_", "OPENAI", "GOOGLE_")
_BROWSER_DENIED_PARTS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL")


def browser_environment() -> dict[str, str | float | bool]:
    """Explicit Playwright launch env: process env minus backend secrets/paths.

    Chromium renders untrusted third-party pages, so it never inherits the
    credential key, service account, OpenAI settings or any *_FILE path.
    """
    return {
        name: value
        for name, value in os.environ.items()
        if not name.upper().startswith(_BROWSER_DENIED_PREFIXES)
        and not name.upper().endswith("_FILE")
        and not any(part in name.upper() for part in _BROWSER_DENIED_PARTS)
    }


def exact_auth_origin(url: str, host: str) -> bool:
    try:
        target = urlsplit(url)
        return (
            target.scheme == "https"
            and target.hostname == host
            and target.port in (None, 443)
            and target.username is None
            and target.password is None
        )
    except ValueError:
        return False


def require_auth_host(url: str) -> None:
    """Fail closed before entering either secret."""
    if not exact_auth_origin(url, "adfs.uwaterloo.ca"):
        raise LoginFlowError("Unexpected authentication origin.")


@dataclass(repr=False)
class LoginSession:
    """Live browser handles, without retained credentials."""

    playwright: Playwright
    context: BrowserContext
    page: Page
    deadline: AuthDeadline
    submitted: bool = False
    closed: bool = False

    def claim_submission(self) -> None:
        if self.submitted or self.closed:
            raise LoginFlowError("Sign-in was already submitted or closed; no retry.")
        self.deadline.remaining_ms()
        self.submitted = True

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            close_browser_context(self.context)
        finally:
            self.playwright.stop()


def secure_profile_directory(profile_path: Path) -> None:
    """Create or tighten the persistent profile to an owner-only real directory.

    Refuses symlinks, non-directories and directories owned by someone else, since
    the profile holds ADFS/Duo trusted-device state. Messages never include paths.
    """
    try:
        profile_path.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = profile_path.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError
        if os.name != "nt":
            if info.st_uid != os.geteuid():
                raise ValueError
            if info.st_mode & 0o077:
                profile_path.chmod(0o700)
    except Exception:
        raise LoginFlowError("Browser profile directory is unsafe.") from None


def close_browser_context(context: BrowserContext) -> None:
    """Drop session cookies, then close; clearing failures never block closing.

    Cookies are cleared on every run so a lingering ADFS/WaterlooWorks session does
    not outlive the bounded login. Device-trust cookies are discarded as well, so a
    later run may need a fresh Duo approval.
    """
    try:
        try:
            context.clear_cookies()
        except Exception:
            pass
    finally:
        context.close()


def service_worker_policy(block: bool) -> Literal["allow", "block"]:
    """Refresh interception requires service workers blocked from context creation."""
    return "block" if block else "allow"


def start_login(
    database_path: Path,
    *,
    project_root: Path,
    block_service_workers: bool = False,
    deadline: AuthDeadline | None = None,
) -> LoginSession:
    """Run the verified flow and return a live browser at the password checkpoint.

    Do not display page contents or record traces.
    No sign-in submission, session export, or job retrieval is performed.
    """
    log = stage_logger("login")
    deadline = deadline or AuthDeadline()
    try:
        # Private lookup only; never exported to the driver/browser environment.
        load_local_environment(project_root / ".env")
    except Exception:
        raise LoginFlowError("Login configuration unavailable.") from None
    if runtime_value("DEBUG") or runtime_value("PWDEBUG"):
        log.error("Disable DEBUG and PWDEBUG before entering credentials.")
        raise LoginFlowError("Disable DEBUG and PWDEBUG before entering credentials.")

    settings = browser_settings()
    try:
        credentials = CredentialStore(database_path).load_for_login()
    except Exception:
        raise LoginFlowError(
            "WaterlooWorks credentials unavailable; check admin "
            "settings and runtime key."
        ) from None

    current_stage = STEPS[0]
    log.info("Starting stage 1/%d: %s", len(STEPS), current_stage)
    playwright: Playwright | None = None
    context: BrowserContext | None = None
    try:
        playwright = sync_playwright().start()
        profile_path = settings.profile
        secure_profile_directory(profile_path)
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=profile_path,
            headless=settings.headless,
            chromium_sandbox=True,
            timeout=deadline.remaining_ms(),
            no_viewport=True,
            service_workers=service_worker_policy(block_service_workers),
            env=browser_environment(),
        )
        page = context.pages[0] if context.pages else context.new_page()
        deadline.bound_page(page)
        if block_service_workers:
            log.debug(
                "Private refresh context; service workers blocked for interception."
            )
        else:
            log.debug(
                "Private persistent profile; browser cache and service workers enabled."
            )
        log.info("Completed stage 1/%d: %s", len(STEPS), current_stage)

        def step(name: str, action: Callable[[], object]) -> None:
            nonlocal current_stage
            current_stage = name
            index = STEPS.index(name) + 1
            log.debug("Waiting %d ms before the next stage.", STEP_DELAY_MS)
            deadline.bound_page(page)
            page.wait_for_timeout(min(STEP_DELAY_MS, deadline.remaining_ms()))
            deadline.bound_page(page)
            log.info("Starting stage %d/%d: %s", index, len(STEPS), name)
            action()
            deadline.remaining_ms()
            log.info("Completed stage %d/%d: %s", index, len(STEPS), name)

        step(STEPS[1], lambda: page.set_viewport_size({"width": 1024, "height": 768}))
        step(
            STEPS[2],
            lambda: page.goto(
                "https://waterlooworks.uwaterloo.ca/", wait_until="commit"
            ),
        )
        step(STEPS[3], lambda: page.wait_for_load_state("load"))

        def student_login() -> None:
            if not exact_auth_origin(page.url, "waterlooworks.uwaterloo.ca"):
                raise LoginFlowError("Unexpected WaterlooWorks origin.")
            page.get_by_role("link", name="Students/Alumni/Staff", exact=True).click(
                no_wait_after=True
            )

        step(STEPS[4], student_login)

        def wait_for_authentication() -> None:
            page.wait_for_url("https://adfs.uwaterloo.ca/**", wait_until="load")
            require_auth_host(page.url)
            deadline.bound_page(page)
            page.locator(USERNAME_SELECTOR).wait_for(state="visible")

        step(STEPS[5], wait_for_authentication)

        def enter_secret(key: str, selector: str) -> None:
            require_auth_host(page.url)
            deadline.bound_page(page)
            field = page.locator(selector)
            field.wait_for(state="visible")
            deadline.bound_page(page)
            secret = credentials.username if key == "username" else credentials.password
            field.fill("")
            deadline.bound_page(page)
            require_auth_host(page.url)
            field.press_sequentially(secret.get_secret_value(), delay=TYPING_DELAY_MS)

        step(STEPS[6], lambda: enter_secret("username", USERNAME_SELECTOR))

        def next_step() -> None:
            require_auth_host(page.url)
            page.get_by_role("button", name="Next", exact=True).click()
            deadline.bound_page(page)
            page.locator("input[type=password]:visible").wait_for(state="visible")

        step(STEPS[7], next_step)
        step(
            STEPS[8],
            lambda: enter_secret("password", "input[type=password]:visible"),
        )
        step(STEPS[9], lambda: None)
        log.info("Password entry complete; returning control to the caller.")
        return LoginSession(playwright, context, page, deadline)
    except Exception:
        # Log only a fixed stage label, never exception text or page data.
        log.error("Failed stage: %s. Sensitive diagnostics suppressed.", current_stage)
        try:
            try:
                if context is not None:
                    close_browser_context(context)
            finally:
                if playwright is not None:
                    playwright.stop()
        except Exception:
            log.warning("Browser cleanup did not complete; diagnostics suppressed.")
        raise LoginFlowError(
            "Login rehearsal stopped; sensitive diagnostics suppressed."
        ) from None
