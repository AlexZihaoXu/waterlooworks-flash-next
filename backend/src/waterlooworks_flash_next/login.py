"""Explicitly invoked, headed login rehearsal that stops before password submission.

Never enable Playwright debug logging, tracing, or screenshots for this flow.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import (
    BrowserContext,
    Page,
    Playwright,
    sync_playwright,
)
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from waterlooworks_flash_next.logging import stage_logger

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


class Credentials(BaseModel):
    """Secrets are excluded from repr and serialization."""

    model_config = ConfigDict(strict=True, hide_input_in_errors=True)
    username: SecretStr = Field(repr=False, exclude=True)
    password: SecretStr = Field(repr=False, exclude=True)

    def __repr__(self) -> str:
        return "Credentials(<redacted>)"


def load_credentials(path: Path) -> Credentials:
    """Read only at authentication runtime; never surface parsing diagnostics."""
    try:
        credentials = Credentials.model_validate_json(path.read_bytes())
        if not credentials.username.get_secret_value().strip():
            raise ValueError
        if not credentials.password.get_secret_value():
            raise ValueError
        return credentials
    except Exception:
        raise LoginFlowError("Credentials are unavailable or invalid.") from None


def require_auth_host(url: str) -> None:
    """Fail closed before entering either secret."""
    target = urlsplit(url)
    if (
        target.scheme != "https"
        or target.hostname != "adfs.uwaterloo.ca"
        or target.port not in (None, 443)
        or target.username is not None
        or target.password is not None
    ):
        raise LoginFlowError("Unexpected authentication origin.")


@dataclass(repr=False)
class LoginSession:
    """Live browser handles, without retained credentials."""

    playwright: Playwright
    context: BrowserContext
    page: Page

    def close(self) -> None:
        try:
            self.context.close()
        finally:
            self.playwright.stop()


def start_login(credentials_path: Path) -> LoginSession:
    """Run the verified flow and return a live browser at the password checkpoint.

    Call from an interactive shell. Do not display page contents or record traces.
    No sign-in submission, session export, or job retrieval is performed.
    """
    log = stage_logger("login")
    if os.environ.get("DEBUG") or os.environ.get("PWDEBUG"):
        log.error("Disable DEBUG and PWDEBUG before entering credentials.")
        raise LoginFlowError("Disable DEBUG and PWDEBUG before entering credentials.")

    current_stage = STEPS[0]
    log.info("Starting stage 1/%d: %s", len(STEPS), current_stage)
    playwright: Playwright | None = None
    try:
        playwright = sync_playwright().start()
        profile_path = (
            credentials_path.resolve().parent / ".experiment" / "browser-profile"
        )
        profile_path.mkdir(parents=True, exist_ok=True, mode=0o700)
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=profile_path,
            headless=False,
            no_viewport=True,
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(30_000)
        page.set_default_navigation_timeout(60_000)
        log.debug(
            "Private persistent profile; browser cache and service workers enabled."
        )
        log.info("Completed stage 1/%d: %s", len(STEPS), current_stage)

        def step(name: str, action: Callable[[], object]) -> None:
            nonlocal current_stage
            current_stage = name
            index = STEPS.index(name) + 1
            log.debug("Waiting %d ms before the next stage.", STEP_DELAY_MS)
            page.wait_for_timeout(STEP_DELAY_MS)
            log.info("Starting stage %d/%d: %s", index, len(STEPS), name)
            action()
            log.info("Completed stage %d/%d: %s", index, len(STEPS), name)

        step(STEPS[1], lambda: page.set_viewport_size({"width": 1024, "height": 768}))
        step(
            STEPS[2],
            lambda: page.goto(
                "https://waterlooworks.uwaterloo.ca/", wait_until="commit"
            ),
        )
        step(STEPS[3], lambda: page.wait_for_load_state("load"))
        step(
            STEPS[4],
            lambda: page.get_by_role(
                "link", name="Students/Alumni/Staff", exact=True
            ).click(no_wait_after=True),
        )

        def wait_for_authentication() -> None:
            page.wait_for_url("https://adfs.uwaterloo.ca/**", wait_until="load")
            require_auth_host(page.url)
            page.locator(USERNAME_SELECTOR).wait_for(state="visible")

        step(STEPS[5], wait_for_authentication)

        def enter_secret(key: str, selector: str) -> None:
            require_auth_host(page.url)
            field = page.locator(selector)
            field.wait_for(state="visible")
            credentials = load_credentials(credentials_path)
            secret = credentials.username if key == "username" else credentials.password
            field.fill("")
            field.press_sequentially(secret.get_secret_value(), delay=TYPING_DELAY_MS)

        step(STEPS[6], lambda: enter_secret("username", USERNAME_SELECTOR))

        def next_step() -> None:
            require_auth_host(page.url)
            page.get_by_role("button", name="Next", exact=True).click()
            page.locator("input[type=password]:visible").wait_for(state="visible")

        step(STEPS[7], next_step)
        step(
            STEPS[8],
            lambda: enter_secret("password", "input[type=password]:visible"),
        )
        step(STEPS[9], lambda: None)
        log.info("Password entry complete; returning control to the caller.")
        return LoginSession(playwright, context, page)
    except Exception:
        # Log only a fixed stage label, never exception text or page data.
        log.error("Failed stage: %s. Sensitive diagnostics suppressed.", current_stage)
        try:
            if playwright is not None:
                playwright.stop()
        except Exception:
            log.warning("Browser cleanup did not complete; diagnostics suppressed.")
        raise LoginFlowError(
            "Login rehearsal stopped; sensitive diagnostics suppressed."
        ) from None
