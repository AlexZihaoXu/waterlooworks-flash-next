"""Durable WaterlooWorks request accounting and refresh admission (schema v15).

Each run counts its actual WaterlooWorks requests in ``collection_run_pacing``. The
rolling 24-hour usage sums runs that ended (or are still running) within the
last day, so a run's requests count for a full day after it finishes. No network
access, credentials or posting data here.
"""

import math
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from waterlooworks_flash_next.refresh_pacing_schema import (
    migrate_refresh_pacing as migrate_refresh_pacing,  # re-exported
)
from waterlooworks_flash_next.ww_pacing import MIN_RUN_REQUESTS, PacingConfig

WINDOW = timedelta(hours=24)
RefusalReason = Literal["blocked", "daily_budget", "cooldown"]


def record_ww_request(connection: sqlite3.Connection, run_id: str) -> None:
    """Persist one request before it is sent; a crash never under-counts usage."""
    with connection:
        connection.execute(
            "INSERT INTO collection_run_pacing(run_id,ww_requests) "
            "SELECT id,1 FROM collection_runs WHERE id=? AND status='running' "
            "ON CONFLICT(run_id) DO UPDATE SET ww_requests=ww_requests+1",
            (run_id,),
        )


def record_pacing_stop(
    connection: sqlite3.Connection, run_id: str, stop: str | None
) -> None:
    """Caller owns the transaction; stores why the run stopped early, if it did."""
    connection.execute(
        "INSERT INTO collection_run_pacing(run_id,pacing_stop) VALUES(?,?) "
        "ON CONFLICT(run_id) DO UPDATE SET pacing_stop=excluded.pacing_stop",
        (run_id, stop),
    )


def _end(row: sqlite3.Row | tuple[object, ...], now: datetime) -> datetime:
    return datetime.fromisoformat(str(row[1])) if row[1] else now


def _window_runs(
    connection: sqlite3.Connection, now: datetime, exclude_run: str | None
) -> list[tuple[datetime, int]]:
    runs: list[tuple[datetime, int]] = []
    for row in connection.execute(
        "SELECT r.id,r.finished_at,p.ww_requests FROM collection_runs r "
        "JOIN collection_run_pacing p ON p.run_id=r.id WHERE p.ww_requests>0"
    ):
        if row[0] == exclude_run:
            continue
        end = _end(row, now)
        if end > now - WINDOW:
            runs.append((end, int(row[2])))
    return sorted(runs)


def rolling_requests(
    connection: sqlite3.Connection, now: datetime, *, exclude_run: str | None = None
) -> int:
    return sum(count for _, count in _window_runs(connection, now, exclude_run))


def describe_delay(seconds: int) -> str:
    if seconds < 3600:
        return f"about {max(1, math.ceil(seconds / 60))} min"
    hours, rest = divmod(seconds, 3600)
    minutes = math.ceil(rest / 60)
    if minutes == 60:
        hours, minutes = hours + 1, 0
    return f"about {hours} h {minutes} min" if minutes else f"about {hours} h"


@dataclass(frozen=True)
class PacingStatus:
    """Hard waits (blocks, rolling budget) and the confirmable post-run interval.

    ``cooldown_until`` is the end of WW_REFRESH_MIN_INTERVAL_MINUTES after the
    latest finish: scheduled refreshes skip within it, and an explicit admin
    refresh within it needs a confirmation. Only blocks and the budget delay a
    confirmed manual refresh, so ``next_allowed_at`` covers those alone.
    """

    config: PacingConfig
    used: int
    remaining: int
    cooldown_until: datetime | None
    blocked_until: datetime | None
    budget_until: datetime | None
    last_finished_at: datetime | None = None

    @property
    def next_allowed_at(self) -> datetime | None:
        values = [
            value
            for value in (self.blocked_until, self.budget_until)
            if value is not None
        ]
        return max(values) if values else None

    def _longest(
        self,
        now: datetime,
        reasons: tuple[tuple[datetime | None, RefusalReason], ...],
    ) -> tuple[RefusalReason, int] | None:
        candidates = [
            (until, reason)
            for until, reason in reasons
            if until is not None and until > now
        ]
        if not candidates:
            return None
        until, reason = max(candidates, key=lambda item: item[0])
        return reason, max(1, math.ceil((until - now).total_seconds()))

    def hard_refusal(self, now: datetime) -> tuple[RefusalReason, int] | None:
        """The longest active block/budget wait; never overridable."""
        return self._longest(
            now,
            ((self.blocked_until, "blocked"), (self.budget_until, "daily_budget")),
        )

    def refusal(self, now: datetime) -> tuple[RefusalReason, int] | None:
        """A hard refusal first; otherwise the active post-run interval, or None."""
        return self.hard_refusal(now) or self._longest(
            now, ((self.cooldown_until, "cooldown"),)
        )


def refusal_message(reason: RefusalReason, seconds: int) -> str:
    delay = describe_delay(seconds)
    if reason == "blocked":
        return (
            "WaterlooWorks refresh is paused after a rate-limit or sign-in "
            f"warning; next refresh allowed in {delay}."
        )
    if reason == "daily_budget":
        return (
            "The rolling 24-hour WaterlooWorks request budget is used up; "
            f"next refresh allowed in {delay}."
        )
    return f"Refresh cooldown is active; next refresh allowed in {delay}."


def recent_message(seconds: int) -> str:
    """Fixed text for an unconfirmed manual refresh within the interval."""
    return (
        "The last refresh finished recently; confirm to refresh again now "
        f"(the minimum interval ends in {describe_delay(seconds)})."
    )


def pacing_status(
    connection: sqlite3.Connection, config: PacingConfig, now: datetime | None = None
) -> PacingStatus:
    now = now or datetime.now(UTC)
    runs = _window_runs(connection, now, None)
    used = sum(count for _, count in runs)
    remaining = max(0, config.daily_request_cap - used)
    budget_until: datetime | None = None
    needed = min(MIN_RUN_REQUESTS, config.daily_request_cap)
    if remaining < needed:
        # Earliest instant enough older runs leave the window to admit a run.
        excess = used - (config.daily_request_cap - needed)
        for end, count in runs:
            excess -= count
            if excess <= 0:
                budget_until = end + WINDOW
                break
    latest = connection.execute(
        "SELECT max(finished_at) FROM collection_runs WHERE finished_at IS NOT NULL"
    ).fetchone()[0]
    last_finished_at = datetime.fromisoformat(latest) if latest else None
    cooldown_until = (
        last_finished_at + timedelta(minutes=config.min_interval_minutes)
        if last_finished_at and config.min_interval_minutes
        else None
    )
    blocked = connection.execute(
        "SELECT max(blocked_until) FROM collection_runs"
    ).fetchone()[0]
    blocked_until = datetime.fromisoformat(blocked) if blocked else None
    return PacingStatus(
        config,
        used,
        remaining,
        cooldown_until if cooldown_until and cooldown_until > now else None,
        blocked_until if blocked_until and blocked_until > now else None,
        budget_until,
        last_finished_at,
    )
