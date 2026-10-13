"""Fixed-format Discord new-job alerts, sent after a refresh finds new postings.

The message lists the top five new postings by maximum CAD/hour pay using the
member-visible workspace data; the rest are summarised in the footer. Delivery
runs on a background thread after the refresh run is finalized, never on a
request path and never able to fail the refresh. Webhook URLs are secrets:
they are only decrypted for delivery and never logged; logs carry the stored
hint (last digits of the webhook ID) and a fixed status only.
"""

import logging
import re
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from waterlooworks_flash_next.agent_search import count_value
from waterlooworks_flash_next.api_models import CompensationSort, PostingResponse
from waterlooworks_flash_next.discord_webhooks import (
    DiscordWebhook,
    WebhookStatus,
    WebhookStore,
    WebhookUnavailable,
)
from waterlooworks_flash_next.logging import stage_logger
from waterlooworks_flash_next.refresh import RunOutcome

USERNAME = "WaterlooWorks Flash"
AVATAR_PATH = "/discord-avatar.png"
TOP_COUNT = 5
TIMEOUT_SECONDS = 10.0
RETRY_AFTER_CAP_SECONDS = 10.0
MAX_WORKERS = 4
# Discord limits: field name 256, field value 1024, all embed text 6000.
# Per-part budgets keep five full fields, the link field and the footer well
# inside the 6000-character total without dropping listed postings.
TITLE_BUDGET = 200
ORGANIZATION_BUDGET = 200
CITY_BUDGET = 100
COUNTRY_BUDGET = 60
PAY_BUDGET = 120
FIELD_VALUE_LIMIT = 1024
ZERO_WIDTH_SPACE = "​"
TEST_TITLE = "Test alert from WaterlooWorks Flash"
TEST_DESCRIPTION = (
    "This webhook is connected. New job alerts are sent here after each "
    "refresh that finds new jobs, listing the top 5 by pay."
)

_NUMBER = re.compile(r"\A[0-9]+(?:\.[0-9]+)?\Z", re.ASCII)
_MARKDOWN = re.compile(r"([\\*_~`|>\[\]()#:<@-])")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")
_HOSTNAME = re.compile(r"\A[a-z0-9]+(?:[.-][a-z0-9]+)*\Z", re.ASCII)
_PERIODS = {"hour": "hr", "day": "day", "week": "wk", "month": "mo", "year": "yr"}


class _DropWebhookUrls(logging.Filter):
    """Never let HTTP client logs carry a webhook URL (its token is a secret)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            return "/api/webhooks/" not in record.getMessage()
        except Exception:
            return False


for _name in ("httpx", "httpcore"):
    _logger = logging.getLogger(_name)
    if not any(isinstance(item, _DropWebhookUrls) for item in _logger.filters):
        _logger.addFilter(_DropWebhookUrls())


# ----- configuration ---------------------------------------------------------


def configured_public_base_url(value: str | None) -> str | None:
    """The site's public origin for links and the avatar, or None when unset.

    Fails closed with a fixed message (never the value) on anything but an
    HTTPS origin; plain HTTP is accepted only for localhost and 127.0.0.1.
    """
    if value is None or value == "":
        return None
    try:
        origin = value[:-1] if value.endswith("/") else value
        url = urlsplit(origin)
        if (
            len(origin) > 200
            or not url.hostname
            or not _HOSTNAME.match(url.hostname)
            or url.username is not None
            or url.password is not None
            or url.path
            or url.query
            or url.fragment
            or origin != f"{url.scheme}://{url.netloc}"
            or url.netloc != url.hostname + (f":{url.port}" if url.port else "")
            or (
                url.scheme != "https"
                and not (
                    url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1"}
                )
            )
        ):
            raise ValueError
        return origin
    except Exception:
        raise ValueError(
            "WW_PUBLIC_BASE_URL must be an HTTPS origin without a path."
        ) from None


# ----- ranking ---------------------------------------------------------------


def max_cad_per_hour(
    pay: CompensationSort | None, fx: Mapping[str, str] | None
) -> Decimal | None:
    """Highest CAD/hour upper bound across a posting's rates, else None.

    Mirrors the web app's salary tiers: an unspecified currency counts as CAD,
    other currencies need a reference rate, weekly/monthly/yearly amounts use
    the stated (else 40) weekly hours and 52 weeks. Any rate without a
    comparable upper bound (open minimum, daily pay, missing FX) or a posting
    needing pay review has no comparable pay.
    """
    if pay is None or pay.needs_review or not pay.source_rates:
        return None
    best: Decimal | None = None
    try:
        for rate in pay.source_rates:
            if rate.bound_type == "minimum":
                return None
            top = rate.minimum if rate.bound_type == "fixed" else rate.maximum
            if top is None or not _NUMBER.match(top):
                return None
            if rate.currency in (None, "CAD"):
                factor = Decimal(1)
            else:
                quoted = (fx or {}).get(str(rate.currency))
                if not isinstance(quoted, str) or not _NUMBER.match(quoted):
                    return None
                factor = Decimal(quoted)
                if factor <= 0:
                    return None
            hours = Decimal(rate.hours_per_week or "40")
            if hours <= 0:
                return None
            amount = Decimal(top)
            if rate.period == "week":
                amount = amount / hours
            elif rate.period == "month":
                amount = amount * 12 / 52 / hours
            elif rate.period == "year":
                amount = amount / 52 / hours
            elif rate.period != "hour":
                return None
            value = amount * factor
            if best is None or value > best:
                best = value
    except (InvalidOperation, ValueError):
        return None
    return best


def rank_by_pay(
    postings: Sequence[PostingResponse], fx: Mapping[str, str] | None
) -> list[PostingResponse]:
    """Highest maximum CAD/hour first; no comparable pay last; ties by ID."""

    def key(posting: PostingResponse) -> tuple[int, Decimal, int]:
        value = max_cad_per_hour(posting.compensation_sort, fx)
        return (
            (0, -value, int(posting.posting_id))
            if value is not None
            else (1, Decimal(0), int(posting.posting_id))
        )

    return sorted(postings, key=key)


# ----- presentation ----------------------------------------------------------


def _amount(value: str) -> str:
    """Web ``compactAmount``: half-up cents, trimmed zeros, grouped thousands."""
    if not _NUMBER.match(value):
        return value
    whole, _, fraction = value.partition(".")
    digits = fraction.ljust(3, "0")
    cents = int(whole) * 100 + int(digits[:2]) + (1 if digits[2] >= "5" else 0)
    if cents == 0 and re.search(r"[1-9]", value):
        return "<0.01"
    decimals = f"{cents % 100:02d}".rstrip("0")
    return f"{cents // 100:,}" + (f".{decimals}" if decimals else "")


def pay_label(pay: CompensationSort | None) -> str:
    """Hourly pay in the original currency, like the web app's default view.

    ``*`` marks an unspecified currency shown as CAD. Differences from the web
    label: an en dash joins ranges, and mixed currencies/periods read "Mixed
    rates" instead of "--/--".
    """
    if pay is None:
        return "Not listed"
    if pay.needs_review:
        return "Needs pay review"
    if pay.minimum is None and pay.maximum is None:
        units = {
            (
                rate.currency,
                "hour" if rate.period in ("week", "month", "year") else rate.period,
            )
            for rate in pay.source_rates
        }
        return "Mixed rates" if len(units) > 1 else "Not listed"
    currency = pay.currency or "CAD"
    unit = f"{currency}/{_PERIODS[pay.period]}" if pay.period else currency
    low = _amount(pay.minimum) if pay.minimum is not None else None
    high = _amount(pay.maximum) if pay.maximum is not None else None
    if low is not None and high is not None:
        amount = low if pay.minimum == pay.maximum else f"{low}–{high}"
        qualifier = ""
    elif low is not None:
        amount, qualifier = low, " (lower bound)"
    else:
        amount, qualifier = high or "", " (upper bound)"
    assumed = (
        any(rate.currency is None for rate in pay.source_rates)
        if pay.source_rates
        else pay.currency is None
    )
    return f"{amount} {unit}{qualifier}" + ("*" if assumed else "")


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(_CONTROL.sub(" ", value).split())
    return cleaned or None


def _location_parts(
    posting: PostingResponse,
) -> tuple[str | None, str | None, str | None]:
    country = posting.country_inference
    return (
        _text(posting.listing.city),
        _text(country.country_name) if country.state == "accepted" else None,
        posting.work_arrangement,
    )


def location_label(posting: PostingResponse) -> str:
    """City and accepted country, then the work arrangement when known."""
    city, country, arrangement = _location_parts(posting)
    place = ", ".join(part for part in (city, country) if part)
    return " · ".join(part for part in (place, arrangement) if part) or "Not listed"


def escape_markdown(text: str, limit: int) -> str:
    """Backslash-escape Discord markdown, truncating to ``limit`` characters.

    Truncation never splits an escape sequence and ends with an ellipsis.
    """
    pieces = [_MARKDOWN.sub(r"\\\1", character) for character in text]
    if sum(len(piece) for piece in pieces) <= limit:
        return "".join(pieces)
    kept: list[str] = []
    size = 0
    for piece in pieces:
        if size + len(piece) > limit - 1:
            break
        kept.append(piece)
        size += len(piece)
    return "".join(kept) + "…"


def _plural(count: int, word: str) -> str:
    return f"{count:,} {word}{'' if count == 1 else 's'}"


def _field(posting: PostingResponse, base_url: str | None) -> dict[str, Any]:
    listing = posting.listing
    lines: list[str] = []
    organization = _text(listing.organization)
    if organization:
        lines += [f"**{escape_markdown(organization, ORGANIZATION_BUDGET)}**", ""]
    posting_id = posting.posting_id
    reference = (
        f"[#{posting_id}]({base_url}/?job={posting_id})"
        if base_url
        else f"#{posting_id}"
    )
    city, country, arrangement = _location_parts(posting)
    place = ", ".join(
        part
        for part in (
            escape_markdown(city, CITY_BUDGET) if city else None,
            escape_markdown(country, COUNTRY_BUDGET) if country else None,
        )
        if part
    )
    location = " · ".join(part for part in (place, arrangement) if part)
    pay = escape_markdown(pay_label(posting.compensation_sort), PAY_BUDGET)
    lines += [
        f"#️⃣ **ID:** {reference}",
        f"📍 **Location:** {location or 'Not listed'}",
        f"💵 **Pay:** {pay}",
    ]
    counts = []
    openings = count_value(listing.openings)
    if openings is not None:
        counts.append(f"🗓️ {_plural(openings, 'opening')}")
    applications = count_value(listing.application_count)
    if applications is not None:
        counts.append(f"💼 {_plural(applications, 'application')}")
    if counts:
        lines += ["", *counts]
    value = "\n".join(lines)
    return {
        "name": escape_markdown(
            _text(listing.title) or "Untitled posting", TITLE_BUDGET
        ),
        "value": value[:FIELD_VALUE_LIMIT],
        "inline": True,
    }


def _identity(base_url: str | None) -> dict[str, Any]:
    payload: dict[str, Any] = {"username": USERNAME}
    if base_url:
        payload["avatar_url"] = base_url + AVATAR_PATH
    return payload


def build_alert(
    new_postings: Sequence[PostingResponse],
    total_jobs: int,
    *,
    base_url: str | None,
    fx: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """The fixed alert message for one refresh run's new postings."""
    count = len(new_postings)
    listed = rank_by_pay(new_postings, fx)[:TOP_COUNT]
    if count == 1:
        summary = "**1 new job**"
    elif count <= TOP_COUNT:
        summary = f"**{count:,} new jobs**, ranked by pay"
    else:
        summary = f"**{count:,} new jobs** · top {TOP_COUNT} by pay"
    fields = [_field(posting, base_url) for posting in listed]
    if base_url:
        host = urlsplit(base_url).netloc
        fields.append(
            {
                "name": ZERO_WIDTH_SPACE,
                "value": f"🔗 [See all new jobs on {host}]({base_url}/)",
                "inline": False,
            }
        )
    site = _plural(total_jobs, "job") + " on the site"
    footer = (
        f"Showing {len(listed)} of {_plural(count, 'new job')} · {site}"
        if len(listed) < count
        else f"{_plural(count, 'new job')} · {site}"
    )
    return {
        **_identity(base_url),
        "content": f":rotating_light: **New job alert**\n{summary}",
        "embeds": [{"color": None, "fields": fields, "footer": {"text": footer}}],
        "allowed_mentions": {"parse": []},
    }


def build_test_message(base_url: str | None) -> dict[str, Any]:
    return {
        **_identity(base_url),
        "embeds": [
            {"title": TEST_TITLE, "description": TEST_DESCRIPTION, "color": None}
        ],
        "allowed_mentions": {"parse": []},
    }


# ----- delivery --------------------------------------------------------------


def _retry_after(response: httpx.Response) -> float:
    value: object = response.headers.get("Retry-After")
    if value is None and len(response.content) <= 4096:
        try:
            body = response.json()
            value = body.get("retry_after") if isinstance(body, dict) else None
        except Exception:
            value = None
    try:
        seconds = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        seconds = 1.0
    if seconds != seconds:  # NaN
        seconds = 1.0
    return min(max(seconds, 0.0), RETRY_AFTER_CAP_SECONDS)


def send_webhook(
    url: str,
    payload: Mapping[str, Any],
    *,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> WebhookStatus:
    """POST one message; never raises and never includes the URL in errors.

    2xx is ok; 401/404 mean the webhook was deleted (gone); one 429 waits for
    Retry-After (capped) and retries once; everything else is failed.
    """
    try:
        with httpx.Client(
            trust_env=False,
            follow_redirects=False,
            timeout=TIMEOUT_SECONDS,
            transport=transport,
            headers={"User-Agent": "WaterlooWorksFlash-Alerts/1.0"},
        ) as client:
            for attempt in range(2):
                response = client.post(url, json=dict(payload))
                status = response.status_code
                if 200 <= status < 300:
                    return "ok"
                if status in (401, 404):
                    return "gone"
                if status == 429 and attempt == 0:
                    sleep(_retry_after(response))
                    continue
                return "failed"
    except Exception:
        return "failed"
    return "failed"


def _default_spawn(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="discord-alerts", daemon=True).start()


class DiscordAlerts:
    """Sends one alert per enabled webhook after each run with new postings."""

    def __init__(
        self,
        path: Path,
        postings: Callable[[], list[PostingResponse]],
        *,
        base_url: str | None,
        fx: Callable[[], Mapping[str, str] | None] = lambda: None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        spawn: Callable[[Callable[[], None]], None] = _default_spawn,
        log: Callable[[str, str], None] | None = None,
        max_workers: int = MAX_WORKERS,
    ) -> None:
        self.path = path
        self.store = WebhookStore(path)
        self.postings = postings
        self.base_url = base_url
        self.fx = fx
        self.transport = transport
        self.sleep = sleep
        self.spawn = spawn
        self.max_workers = max_workers
        self._log = log or self._stage_log
        self._lock = threading.Lock()

    @staticmethod
    def _stage_log(message: str, level: str) -> None:
        logger = stage_logger("discord-alerts")
        (logger.warning if level == "warning" else logger.info)(message)

    def _send(self, url: str, payload: Mapping[str, Any]) -> WebhookStatus:
        return send_webhook(url, payload, transport=self.transport, sleep=self.sleep)

    def run_finished(self, outcome: RunOutcome) -> None:
        """Refresh listener: schedule delivery off the worker; never raises.

        Interrupted runs (server shutdown) send nothing.
        """
        try:
            if outcome.status == "interrupted":
                return
            run_id = outcome.run_id
            self.spawn(lambda: self._dispatch_quietly(run_id))
        except Exception:
            self._log(
                "Discord alerts could not start; diagnostics suppressed.", "warning"
            )

    def _dispatch_quietly(self, run_id: str) -> None:
        try:
            self.dispatch(run_id)
        except Exception:
            self._log("Discord alerts failed; diagnostics suppressed.", "warning")

    def _new_posting_ids(self, run_id: str) -> tuple[set[str], bool]:
        connection = sqlite3.connect(
            self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5
        )
        try:
            connection.execute("BEGIN")
            new = {
                row[0]
                for row in connection.execute(
                    "SELECT posting_id FROM postings WHERE first_seen_run_id=?",
                    (run_id,),
                )
            }
            earlier = connection.execute(
                "SELECT EXISTS(SELECT 1 FROM postings WHERE first_seen_run_id<>?)",
                (run_id,),
            ).fetchone()[0]
            return new, bool(earlier)
        finally:
            connection.close()

    def _rates(self, postings: Sequence[PostingResponse]) -> Mapping[str, str] | None:
        foreign = any(
            rate.currency not in (None, "CAD")
            for posting in postings
            if posting.compensation_sort is not None
            for rate in posting.compensation_sort.source_rates
        )
        if not foreign:
            return None
        try:
            return self.fx()
        except Exception:
            return None

    def dispatch(self, run_id: str) -> int:
        """Send this run's alert; returns the number of webhooks attempted.

        Nothing is sent when the run added no postings, or when it is the
        first collection (every saved posting would otherwise be "new").
        """
        with self._lock:
            new_ids, earlier = self._new_posting_ids(run_id)
            if not new_ids or not earlier:
                return 0
            snapshot = self.postings()
            new = [posting for posting in snapshot if posting.posting_id in new_ids]
            if not new:
                return 0
            try:
                targets = self.store.enabled_targets()
            except WebhookUnavailable:
                self._log(
                    "Discord alerts skipped: the backend encryption key is "
                    "unavailable.",
                    "warning",
                )
                return 0
            if not targets:
                return 0
            payload = build_alert(
                new, len(snapshot), base_url=self.base_url, fx=self._rates(new)
            )
            with ThreadPoolExecutor(
                max_workers=max(1, min(self.max_workers, len(targets))),
                thread_name_prefix="discord-alert",
            ) as pool:
                results = list(
                    pool.map(
                        lambda target: self._send(
                            target.url.get_secret_value(), payload
                        ),
                        targets,
                    )
                )
            for target, status in zip(targets, results, strict=True):
                try:
                    self.store.record(target.id, status)
                except Exception:
                    self._log(
                        f"Could not record the Discord alert result for webhook "
                        f"{target.hint}; diagnostics suppressed.",
                        "warning",
                    )
                self._log(
                    f"Discord alert to webhook {target.hint}: {status}.",
                    "info" if status == "ok" else "warning",
                )
            return len(targets)

    def send_test(self, uid: str, webhook_id: int) -> DiscordWebhook:
        """Send the fixed test message to one of the caller's own webhooks."""
        target = self.store.target(uid, webhook_id)
        status = self._send(
            target.url.get_secret_value(), build_test_message(self.base_url)
        )
        self.store.record(target.id, status)
        self._log(
            f"Discord test alert to webhook {target.hint}: {status}.",
            "info" if status == "ok" else "warning",
        )
        return self.store.view(uid, webhook_id)
