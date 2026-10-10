"""Fixed-marker recognition of WaterlooWorks "unhappy site" signals.

Dependency-free so both the transports (``listings``/``detail_fetch``) and the
pacing gate can use it. Only booleans/fixed labels leave this module; header
values and page text are never logged or stored.
"""

from typing import Literal

# HTTP 429: refreshes stay blocked for at least this long (or Retry-After).
RATE_LIMIT_BLOCK_SECONDS = 60 * 60
# 401/403, sign-in/SSO/Duo redirect or page, challenge page, repeated transient
# failures: refreshes stay blocked this long.
UNHAPPY_BLOCK_SECONDS = 6 * 60 * 60

_LOGIN_MARKERS = (
    "adfs.",
    "duosecurity",
    "notloggedin",
    "login",
    "logon",
    "signin",
    "sign-in",
    "/sso",
    "saml",
    "shibboleth",
    "/cas/",
)
_RATE_LIMIT_MARKERS = (
    "captcha",
    "unusual activity",
    "unusual traffic",
    "too many requests",
    "rate limit",
    "rate-limit",
    "are you a robot",
    "request blocked",
    "temporarily blocked",
    "access denied",
)
_SIGN_IN_MARKERS = (
    "adfs.uwaterloo.ca",
    "duosecurity.com",
    "notloggedin",
    "session has expired",
    "session expired",
    "please log in",
    "please sign in",
)


def login_redirect(location: str | None) -> bool:
    """A mid-run redirect toward sign-in, SSO or Duo (header value never logged)."""
    if not location:
        return False
    lowered = location.lower()
    return any(marker in lowered for marker in _LOGIN_MARKERS)


def unhappy_page(text: str) -> Literal["rate_limit", "sign_in"] | None:
    """Classify an *unexpected* response body; only called after validation fails.

    Expected listing JSON or posting markup is never sniffed, so ordinary posting
    text mentioning these words cannot trigger a block.
    """
    lowered = text[: 512 * 1024].lower()
    if any(marker in lowered for marker in _RATE_LIMIT_MARKERS):
        return "rate_limit"
    if any(marker in lowered for marker in _SIGN_IN_MARKERS):
        return "sign_in"
    return None
