"""Fixed-format Discord new-job alerts for postings ready to show.

After every completed or partial refresh, each enabled webhook gets at most
one message about postings first seen since it was added that are now ready
(details saved and AI extraction finished; see ``alert_readiness``) and not
yet announced to it. The message has one embed for each of the top five by
maximum CAD/hour pay, using the member-visible workspace data; the rest are
summarised in the last footer. Delivery runs on a background thread after the
refresh run is finalized, never on a request path and never able to fail the
refresh. Webhook URLs are secrets: they are only decrypted for delivery and
never logged; logs carry the stored hint (last digits of the webhook ID), a
fixed status and a job count only.
"""

import logging
import re
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from waterlooworks_flash_next.agent_search import count_value
from waterlooworks_flash_next.alert_readiness import (
    ai_extraction_active,
    ready_posting_ids,
)
from waterlooworks_flash_next.api_models import CompensationSort, PostingResponse
from waterlooworks_flash_next.discord_webhooks import (
    DiscordWebhook,
    WebhookStatus,
    WebhookStore,
    WebhookTarget,
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
# Postings first seen longer ago are never alerted (retired silently).
STALE_AFTER = timedelta(days=7)
# Discord limits: title 256, description 4096, footer 2048, 10 embeds and
# 6000 characters across all embeds. Per-part budgets keep five full job
# embeds (about 1000 characters each at most) inside the 6000 total.
TITLE_LIMIT = 256
DESCRIPTION_LIMIT = 4096
FOOTER_LIMIT = 2048
ORGANIZATION_BUDGET = 200
CITY_BUDGET = 100
COUNTRY_BUDGET = 60
PAY_BUDGET = 120
# The web app's salary tiers (frontend/src/lib/salaryTier.ts) by maximum
# CAD/hour, using the dark-theme colors from .salary-* in index.css (Discord's
# default theme is dark). Below 20 CAD/hour, or without comparable pay: none.
TIER_COLORS = (
    (Decimal(65), 0xFF5252),  # red and top
    (Decimal(50), 0xD6A3FF),  # purple
    (Decimal(40), 0xFFD166),  # gold
    (Decimal(30), 0x8AB4FF),  # blue
    (Decimal(20), 0x75DFA1),  # green
)
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


def tier_color(
    pay: CompensationSort | None, fx: Mapping[str, str] | None
) -> int | None:
    """The web app's salary tier color for the maximum CAD/hour, else None."""
    value = max_cad_per_hour(pay, fx)
    if value is None:
        return None
    for threshold, color in TIER_COLORS:
        if value >= threshold:
            return color
    return None


def _description(posting: PostingResponse) -> list[str]:
    listing = posting.listing
    lines: list[str] = []
    organization = _text(listing.organization)
    if organization:
        lines.append(f"**{escape_markdown(organization, ORGANIZATION_BUDGET)}**")
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
    if location:
        lines.append(f"📍 {location}")
    lines.append(
        f"💵 {escape_markdown(pay_label(posting.compensation_sort), PAY_BUDGET)}"
    )
    counts = []
    openings = count_value(listing.openings)
    if openings is not None:
        counts.append(f"🗓️ {_plural(openings, 'opening')}")
    applications = count_value(listing.application_count)
    if applications is not None:
        counts.append(f"💼 {_plural(applications, 'application')}")
    if counts:
        lines.append(" · ".join(counts))
    return lines


def _embed(
    posting: PostingResponse,
    base_url: str | None,
    fx: Mapping[str, str] | None,
    *,
    footer: str,
    extra: str | None = None,
) -> dict[str, Any]:
    posting_id = posting.posting_id
    embed: dict[str, Any] = {
        "title": escape_markdown(
            _text(posting.listing.title) or "Untitled posting", TITLE_LIMIT
        )
    }
    if base_url:
        embed["url"] = f"{base_url}/?job={posting_id}"
    lines = _description(posting) + ([extra] if extra else [])
    embed["color"] = tier_color(posting.compensation_sort, fx)
    embed["description"] = "\n".join(lines)[:DESCRIPTION_LIMIT]
    embed["footer"] = {"text": f"#{posting_id}{footer}"[:FOOTER_LIMIT]}
    return embed


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
    pending: int = 0,
) -> dict[str, Any]:
    """One webhook's alert: an embed for each of the top five ready postings.

    ``new_postings`` are the ready, not yet announced postings for this
    webhook; ``pending`` counts newer postings still waiting for details or
    extraction, mentioned in the last footer.
    """
    count = len(new_postings)
    listed = rank_by_pay(new_postings, fx)[:TOP_COUNT]
    if count == 1:
        summary = "**1 new job**"
    elif count <= TOP_COUNT:
        summary = f"**{count:,} new jobs**, ranked by pay"
    else:
        summary = f"**{count:,} new jobs** · top {TOP_COUNT} by pay"
    site = _plural(total_jobs, "job") + " on the site"
    footer = (
        f" · Showing {len(listed)} of {_plural(count, 'new job')} · {site}"
        if len(listed) < count
        else f" · {_plural(count, 'new job')} · {site}"
    )
    if pending > 0:
        footer += f" · {pending:,} still being analysed"
    link = (
        f"🔗 [See all new jobs on {urlsplit(base_url).netloc}]({base_url}/)"
        if base_url
        else None
    )
    last = len(listed) - 1
    embeds = [
        _embed(
            posting,
            base_url,
            fx,
            footer=footer if index == last else "",
            extra=link if index == last else None,
        )
        for index, posting in enumerate(listed)
    ]
    return {
        **_identity(base_url),
        "content": f":rotating_light: **New job alert**\n{summary}",
        "embeds": embeds,
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


@dataclass(frozen=True)
class _RunPostings:
    """Postings listed in one run, from a single read snapshot."""

    first_seen: dict[str, datetime]
    ready: set[str]


@dataclass(frozen=True)
class _Plan:
    target: WebhookTarget
    candidates: list[PostingResponse]
    pending: int
    stale: set[str]


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


class DiscordAlerts:
    """After each run, alerts every enabled webhook about its ready new postings."""

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
        ai_active: Callable[[], bool] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
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
        self.ai_active = ai_active or (lambda: ai_extraction_active(path))
        self.clock = clock
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

    def _run_postings(self, run_id: str, ai_active: bool) -> _RunPostings:
        """Postings listed in this run, excluding the first collection's.

        The earliest run that saved postings is the baseline: everything it
        saved already existed, so none of it is ever alerted.
        """
        connection = sqlite3.connect(
            self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5
        )
        try:
            connection.execute("BEGIN")
            baseline = connection.execute(
                "SELECT r.id FROM collection_runs r WHERE EXISTS "
                "(SELECT 1 FROM postings p WHERE p.first_seen_run_id=r.id) "
                "ORDER BY r.started_at, r.rowid LIMIT 1"
            ).fetchone()
            first_seen: dict[str, datetime] = {}
            for posting_id, seen, first_run in connection.execute(
                "SELECT p.posting_id,p.first_seen_at,p.first_seen_run_id "
                "FROM posting_observations o JOIN postings p USING(posting_id) "
                "WHERE o.run_id=?",
                (run_id,),
            ):
                parsed = _parse_time(seen)
                if parsed is not None and (
                    baseline is None or first_run != baseline[0]
                ):
                    first_seen[str(posting_id)] = parsed
            ready = (
                ready_posting_ids(connection, ai_active=ai_active) & first_seen.keys()
                if first_seen
                else set()
            )
            return _RunPostings(first_seen, ready)
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

    def _plan(
        self,
        targets: Sequence[WebhookTarget],
        run: _RunPostings,
        snapshot: Mapping[str, PostingResponse],
    ) -> list[_Plan]:
        """Per webhook: ready unannounced postings, pending count, stale IDs.

        Only postings first seen since the webhook was added count; those
        first seen more than ``STALE_AFTER`` ago are retired silently.
        """
        cutoff = self.clock() - STALE_AFTER
        done = self.store.announced([target.id for target in targets])
        plans = []
        for target in targets:
            added = datetime.fromtimestamp(target.created_at, UTC).replace(
                microsecond=0
            )
            unannounced = {
                posting_id: seen
                for posting_id, seen in run.first_seen.items()
                if seen >= added and posting_id not in done[target.id]
            }
            stale = {pid for pid, seen in unannounced.items() if seen < cutoff}
            fresh = unannounced.keys() - stale
            ready = fresh & run.ready
            plans.append(
                _Plan(
                    target,
                    [
                        snapshot[posting_id]
                        for posting_id in sorted(ready, key=int)
                        if posting_id in snapshot
                    ],
                    len(fresh - ready),
                    stale,
                )
            )
        return plans

    def dispatch(self, run_id: str) -> int:
        """Alert each enabled webhook; returns the number of webhooks sent to.

        Candidates for a webhook are postings listed in this run, first seen
        since the webhook was added (never the first collection's), ready
        (``alert_readiness``), in the member workspace, not yet announced to
        it, and first seen within ``STALE_AFTER``. They are marked announced
        only after Discord accepts the message, so a failed delivery retries
        after the next run. Webhooks without candidates get nothing.
        """
        with self._lock:
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
            run = self._run_postings(run_id, self.ai_active())
            if not run.first_seen:
                return 0
            snapshot_list = self.postings()
            snapshot = {posting.posting_id: posting for posting in snapshot_list}
            plans = self._plan(targets, run, snapshot)
            for plan in plans:
                self.store.mark_announced(plan.target.id, plan.stale)
            sending = [plan for plan in plans if plan.candidates]
            if not sending:
                return 0
            fx = self._rates([p for plan in sending for p in plan.candidates])
            payloads = [
                build_alert(
                    plan.candidates,
                    len(snapshot_list),
                    base_url=self.base_url,
                    fx=fx,
                    pending=plan.pending,
                )
                for plan in sending
            ]
            with ThreadPoolExecutor(
                max_workers=max(1, min(self.max_workers, len(sending))),
                thread_name_prefix="discord-alert",
            ) as pool:
                results = list(
                    pool.map(
                        lambda plan, payload: self._send(
                            plan.target.url.get_secret_value(), payload
                        ),
                        sending,
                        payloads,
                    )
                )
            for plan, status in zip(sending, results, strict=True):
                target = plan.target
                try:
                    self.store.record(target.id, status)
                    if status == "ok":
                        self.store.mark_announced(
                            target.id, (p.posting_id for p in plan.candidates)
                        )
                except Exception:
                    self._log(
                        f"Could not record the Discord alert result for webhook "
                        f"{target.hint}; diagnostics suppressed.",
                        "warning",
                    )
                self._log(
                    f"Discord alert to webhook {target.hint}: {status} "
                    f"({_plural(len(plan.candidates), 'job')}).",
                    "info" if status == "ok" else "warning",
                )
            return len(sending)

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
