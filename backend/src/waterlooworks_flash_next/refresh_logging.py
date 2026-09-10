"""Reviewed refresh log formats and fixed text; never format arbitrary log payloads.

Update this allowlist when adding a stage log. Unknown arguments are redacted,
not guessed safe. Exception/stack text and authentication values never enter SSE.
"""

import logging
import math
import re

from waterlooworks_flash_next.listings import FailureCategory
from waterlooworks_flash_next.login import STEPS

FORMATS = frozenset(
    {
        "Authentication stopped; no automatic retry.",
        "Accepted optional device trust prompt; waiting for WaterlooWorks.",
        "All Jobs click completed; checking captured listing responses.",
        "Batch %d/%d: pages %d-%d (sequential).",
        "Board stopped | stage=%s | error_type=%s; no retry.",
        "Browser cleanup did not complete; diagnostics suppressed.",
        "Browser cleanup failed; sensitive diagnostics suppressed.",
        "Checking existing DOM for code (%d/%d).",
        "Completed stage %d/%d: %s",
        "Completed stage 1/%d: %s",
        "Completed stage: job-board page loaded.",
        "Completed stage: sign in. Observing Duo or WaterlooWorks via DOM only.",
        "Dashboard not confirmed; skipping job-board navigation.",
        "Disable DEBUG and PWDEBUG before entering credentials.",
        "Duo approval required; open Admin settings from the account button.",
        "Failed stage: %s. Sensitive diagnostics suppressed.",
        (
            "Fetch finished: page %d/%d | received %d | records %d | "
            "unique %d | duplicates %d | ETA %s%s"
        ),
        "Listing did not settle within 30s; no replay or extra click.",
        "Listing pass complete: %d pages, %d unique postings. No detail requests.",
        "Listing total changed; retaining the initial page bound.",
        "Page %d attempt %d/%d failed | category=%s | HTTP=%s | retry=%s",
        "Page 1 captured | responses=%d | after-click responses=%d.",
        "Password entry complete; returning control to the caller.",
        "Private persistent profile; browser cache and service workers enabled.",
        "Private refresh context; service workers blocked for interception.",
        "Retrying the same page after %.0fs from failure completion.",
        "Starting stage %d/%d: %s",
        "Starting stage 1/%d: %s",
        "Starting stage: click All Jobs once.",
        "Starting stage: open Full Cycle Service Job Board; capture active.",
        "Starting stage: sign in (single submission).",
        "Unrecognized authentication destination; pausing for guidance.",
        "Waiting %d ms before the next stage.",
        "WaterlooWorks reached: Dashboard and logout controls visible.",
        "category=%s | %s Completed pages: %d; unique postings retained: %d.",
    }
)

SAFE_TEXT = frozenset(STEPS) | frozenset(
    {
        "",
        " (captured browser response, no replay)",
        "Authentication transition exceeded 60 seconds.",
        "Could not capture a valid page-1 listing request.",
        "Duo observation stopped; sensitive diagnostics suppressed.",
        "Duo rejected the verification code.",
        "Duo request timed out.",
        "Error",
        "Listing capture is stale; start a fresh login.",
        "Listing fetch failed; diagnostics suppressed.",
        "Listing pagination response did not match request.",
        "Listing request timed out.",
        "Listing response exceeded the size limit.",
        "Listing response validation failed.",
        "Listing response was interrupted.",
        "Listing row has an invalid posting ID.",
        "Listing source already used; automatic restart disabled.",
        "Listing transport failed.",
        "ListingFetchError",
        "Page added no new IDs; stopped to avoid repeated fetching.",
        "Response cannot be decoded with an allowed declared charset.",
        "RuntimeError",
        "TLS validation or connection failed.",
        "TimeoutError",
        "Unexpected empty listing page; partial results retained.",
        "ValueError",
        "Verification code not detected after 10 DOM checks.",
        "click All Jobs",
        "confirm dashboard",
        "estimating",
        "locate All Jobs button",
        "n/a",
        "open board",
        "validate latest captured response",
        "wait for existing listing response to finish",
    }
)

PAGE_FORMAT = (
    "Fetch finished: page %d/%d | received %d | records %d | "
    "unique %d | duplicates %d | ETA %s%s"
)


def safe_eta_seconds(value: object) -> int | None:
    """Accept only the collector's reviewed duration format, never arbitrary text."""
    if type(value) is not str or not re.fullmatch(r"\d{2,6}:[0-5]\d:[0-5]\d", value):
        return None
    hours, minutes, seconds = map(int, value.split(":"))
    return hours * 3600 + minutes * 60 + seconds


def safe_message(record: logging.LogRecord) -> str:
    """Preserve reviewed text, without invoking arbitrary payload formatting."""
    scope = (
        record.name
        if record.name in {"login", "duo", "jobs", "listings"}
        else "refresh"
    )
    prefix = f"[{scope}] "
    message = record.msg
    if type(message) is not str:
        return prefix + "Unreviewed log content withheld."
    if message.startswith("Detected verification code:"):
        return prefix + (
            "Duo approval required; open Admin settings from the account button."
        )
    if not record.args and re.fullmatch(
        r"Detail capture blocked: resource=(image|font|stylesheet|media|texttrack|"
        r"script|document|xhr|fetch|other|unknown); method=(get|post|other|unknown); "
        r"phase=(idle|active|completed); disposition=(discarded|fatal); "
        r"count=(0|[1-9][0-9]{0,4}|100000)\.",
        message,
    ):
        return prefix + message
    if message not in FORMATS:
        return prefix + "Unreviewed log content withheld."
    if not isinstance(record.args, tuple):
        return prefix + "Invalid log arguments withheld."
    safe: list[object] = []
    for index, value in enumerate(record.args):
        if (
            isinstance(value, int)
            and type(value) in (int, bool)
            and abs(value) <= 10**12
        ):
            safe.append(value)
        elif type(value) is float and math.isfinite(value) and 0 <= value <= 86400:
            safe.append(value)
        elif isinstance(value, FailureCategory):
            safe.append(value.value)
        elif type(value) is str and (
            value in SAFE_TEXT
            or (
                message == PAGE_FORMAT
                and index == 6
                and re.fullmatch(r"\d{2,6}:[0-5]\d:[0-5]\d", value)
            )
            or (
                message.startswith("category=%s |")
                and index == 1
                and re.fullmatch(r"Listing HTTP [1-5]\d{2}\.", value)
            )
        ):
            safe.append(value)
        else:
            safe.append("[redacted]")
    try:
        return prefix + (message % tuple(safe) if safe else message)
    except (TypeError, ValueError, OverflowError):
        return prefix + "Invalid log arguments withheld."
