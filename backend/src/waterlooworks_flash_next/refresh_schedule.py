"""Admin-scheduled automatic refreshes in Toronto wall-clock time.

Times are stored as minutes of the Toronto day. A scheduled time becomes a
*slot* on each local date, keyed ``YYYY-MM-DD HH:MM``; a slot fires at most
once, ever, because claiming it is one INSERT on that primary key. DST: a
nonexistent spring-forward time fires once at the shifted instant (the
standard-time reading, 02:30 -> 03:30 EDT); a repeated fall-back time fires
once at its first occurrence.

Validation errors are fixed strings that never echo input.
"""

import asyncio
import re
import sqlite3
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

from waterlooworks_flash_next.api_models import (
    RefreshScheduleView,
    ScheduleFailureView,
    ScheduleSlotView,
)
from waterlooworks_flash_next.credentials import CredentialError
from waterlooworks_flash_next.database import open_database
from waterlooworks_flash_next.logging import stage_logger
from waterlooworks_flash_next.refresh import RefreshConflict, RunOutcome

TORONTO = ZoneInfo("America/Toronto")
MAX_TIMES = 24
GRACE = timedelta(minutes=10)
TICK_SECONDS = 30
_TIME = re.compile(r"\A([01][0-9]|2[0-3]):([0-5][0-9])\Z", re.ASCII)

SlotOutcome = Literal[
    "claimed",
    "started",
    "skipped_running",
    "skipped_blocked",
    "skipped_disabled",
    "failed_to_start",
]
FailureCategory = Literal[
    "sign_in", "authentication", "rate_limit", "redirect", "ban_risk"
]


class ScheduleError(ValueError):
    """Fixed, input-free validation message safe to return to the admin."""


def parse_time(text: str) -> int:
    match = _TIME.match(text) if isinstance(text, str) else None
    if match is None:
        raise ScheduleError("Time must be HH:MM in 24-hour Toronto time.")
    return int(match[1]) * 60 + int(match[2])


def format_minute(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def validate_new_time(existing: Iterable[int], minute: int, min_interval: int) -> None:
    current = list(existing)
    if minute in current:
        raise ScheduleError("That time is already scheduled.")
    if len(current) >= MAX_TIMES:
        raise ScheduleError(f"At most {MAX_TIMES} scheduled times are allowed.")
    for other in current:
        gap = abs(other - minute)
        if min(gap, 1440 - gap) < min_interval:
            raise ScheduleError(
                f"Scheduled times must be at least {min_interval} minutes apart."
            )


def slot_key(local_date: date, minute: int) -> str:
    return f"{local_date.isoformat()} {format_minute(minute)}"


def slot_instant(local_date: date, minute: int) -> datetime:
    """UTC instant of a Toronto wall-clock time (fold=0; see module notes)."""
    wall = datetime.combine(local_date, time(minute // 60, minute % 60), TORONTO)
    return wall.astimezone(UTC)


def due_slots(
    now: datetime,
    minutes: Iterable[int],
    enabled_at: datetime | None,
    grace: timedelta = GRACE,
) -> list[tuple[str, datetime]]:
    """Slots due at ``now``: within the grace window and not before enabling.

    Older missed slots (downtime, a disabled period) are never caught up.
    """
    today = now.astimezone(TORONTO).date()
    due: list[tuple[str, datetime]] = []
    for local_date in (today - timedelta(days=1), today):
        for minute in minutes:
            instant = slot_instant(local_date, minute)
            if not instant <= now < instant + grace:
                continue
            if enabled_at is not None and instant < enabled_at:
                continue
            due.append((slot_key(local_date, minute), instant))
    return sorted(due, key=lambda item: item[1])


def next_run(
    now: datetime, minutes: Iterable[int], enabled_at: datetime | None
) -> datetime | None:
    times = list(minutes)
    today = now.astimezone(TORONTO).date()
    candidates = [
        instant
        for offset in range(3)
        for minute in times
        if (instant := slot_instant(today + timedelta(days=offset), minute)) > now
        and (enabled_at is None or instant >= enabled_at)
    ]
    return min(candidates, default=None)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _parse(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value).astimezone(UTC)


@dataclass(frozen=True)
class SlotRecord:
    slot: str
    due_at: datetime
    outcome: SlotOutcome
    run_id: str | None
    decided_at: datetime
    failure_category: FailureCategory | None


@dataclass(frozen=True)
class ScheduleFailure:
    run_id: str
    slot: str
    failed_at: datetime
    category: FailureCategory


@dataclass(frozen=True)
class ScheduleState:
    enabled: bool
    enabled_at: datetime | None
    failure: ScheduleFailure | None


class ScheduleStore:
    """Short-lived connections per call; safe across threads and processes."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        return open_database(self.path)

    def minutes(self) -> list[int]:
        connection = self._connect()
        try:
            return [
                row[0]
                for row in connection.execute(
                    "SELECT minute_of_day FROM refresh_schedule_times "
                    "ORDER BY minute_of_day"
                )
            ]
        finally:
            connection.close()

    def add(self, minute: int, min_interval: int, now: datetime | None = None) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = [
                    row[0]
                    for row in connection.execute(
                        "SELECT minute_of_day FROM refresh_schedule_times"
                    )
                ]
                validate_new_time(existing, minute, min_interval)
                connection.execute(
                    "INSERT INTO refresh_schedule_times VALUES(?,?)",
                    (minute, _iso(now or datetime.now(UTC))),
                )
        finally:
            connection.close()

    def remove(self, minute: int) -> bool:
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    "DELETE FROM refresh_schedule_times WHERE minute_of_day=?",
                    (minute,),
                )
            return cursor.rowcount == 1
        finally:
            connection.close()

    def claim(self, slot: str, due_at: datetime, now: datetime) -> bool:
        """Atomically claim a slot; False when any worker already decided it."""
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO refresh_schedule_slots"
                    "(slot,due_at,outcome,decided_at) VALUES(?,?,'claimed',?)",
                    (slot, _iso(due_at), _iso(now)),
                )
            return cursor.rowcount == 1
        finally:
            connection.close()

    def record(
        self, slot: str, outcome: SlotOutcome, run_id: str | None, now: datetime
    ) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    "UPDATE refresh_schedule_slots SET outcome=?, run_id=?, "
                    "decided_at=? WHERE slot=? AND outcome='claimed'",
                    (outcome, run_id, _iso(now), slot),
                )
        finally:
            connection.close()

    def recent(self, limit: int = 5) -> list[SlotRecord]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT slot,due_at,outcome,run_id,decided_at,failure_category "
                "FROM refresh_schedule_slots ORDER BY due_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            connection.close()
        return [
            SlotRecord(
                slot=row[0],
                due_at=datetime.fromisoformat(row[1]),
                outcome=row[2],
                run_id=row[3],
                decided_at=datetime.fromisoformat(row[4]),
                failure_category=row[5],
            )
            for row in rows
        ]

    def state(self) -> ScheduleState:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT enabled,enabled_at,failed_run_id,failed_slot,failed_at,"
                "failure_category FROM refresh_schedule_state WHERE id=1"
            ).fetchone()
        finally:
            connection.close()
        failure = (
            ScheduleFailure(
                run_id=row[2],
                slot=row[3],
                failed_at=datetime.fromisoformat(row[4]),
                category=row[5],
            )
            if row[2] is not None
            else None
        )
        return ScheduleState(
            enabled=bool(row[0]), enabled_at=_parse(row[1]), failure=failure
        )

    def set_enabled(self, enabled: bool, now: datetime | None = None) -> None:
        """Turning on clears the failure banner (history stays in the slots)."""
        at = _iso(now or datetime.now(UTC))
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    "UPDATE refresh_schedule_state SET enabled=?, enabled_at=?, "
                    "disabled_reason=?, failed_run_id=CASE WHEN ? THEN NULL "
                    "ELSE failed_run_id END, failed_slot=CASE WHEN ? THEN NULL "
                    "ELSE failed_slot END, failed_at=CASE WHEN ? THEN NULL "
                    "ELSE failed_at END, failure_category=CASE WHEN ? THEN NULL "
                    "ELSE failure_category END, changed_at=? WHERE id=1",
                    (
                        int(enabled),
                        at if enabled else None,
                        None if enabled else "admin",
                        enabled,
                        enabled,
                        enabled,
                        enabled,
                        at,
                    ),
                )
        finally:
            connection.close()

    def fail_scheduled_run(
        self, run_id: str, category: FailureCategory, now: datetime
    ) -> bool:
        """Atomically turn scheduling off for a scheduled run's bad failure.

        Returns False (and changes nothing) when the run was not scheduled.
        """
        connection = self._connect()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT slot,due_at FROM refresh_schedule_slots WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                if row is None:
                    return False
                connection.execute(
                    "UPDATE refresh_schedule_slots SET failure_category=? "
                    "WHERE run_id=?",
                    (category, run_id),
                )
                connection.execute(
                    "UPDATE refresh_schedule_state SET enabled=0, enabled_at=NULL, "
                    "disabled_reason='run_failed', failed_run_id=?, failed_slot=?, "
                    "failed_at=?, failure_category=?, changed_at=? WHERE id=1",
                    (run_id, row[0], row[1], category, _iso(now)),
                )
            return True
        finally:
            connection.close()


_BAD_ERRORS: frozenset[str] = frozenset({"authentication", "rate_limit", "redirect"})


def scheduled_failure(outcome: RunOutcome) -> FailureCategory | None:
    """Failures that must turn scheduling off; ordinary ones return None.

    Any ban-risk block wins. A failure before WaterlooWorks confirmed the
    sign-in is a Duo/login/credential problem (approval denied, timed out or
    not completed). After sign-in, 401/403 or a sign-in page (authentication),
    429 or a challenge page (rate_limit) and a login/SSO redirect mid-run are
    bad. Network, timeout, caps and shutdown interruptions are not.
    """
    if outcome.status != "failed":
        return None
    if outcome.pacing_stop == "ban_risk":
        return "ban_risk"
    if not outcome.authenticated:
        return "sign_in"
    category = outcome.error_category
    if category in _BAD_ERRORS:
        return category  # type: ignore[return-value]
    return None


class ScheduledService(Protocol):
    def start(self) -> str: ...

    def emit(self, message: str, level: str = "info") -> None: ...

    def set_schedule_notice(
        self, next_at: str | None, failed_at: str | None
    ) -> None: ...


def _utc_iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


class RefreshScheduler:
    """Fires due slots through the exact manual-refresh path (service.start).

    Every existing protection applies: the provider lease, the shared pacing
    gate and caps, the minimum interval, ban-risk blocks, credential and AI
    readiness checks and the post-run cooldown. A refused slot is recorded
    and never retried; slots older than the grace window are never caught up.
    ``active`` is the server gate (WW_ENABLE_REFRESH and WW_ENABLE_SCHEDULE).
    """

    def __init__(
        self,
        store: ScheduleStore,
        service: ScheduledService,
        *,
        active: bool,
        min_interval_minutes: int = 30,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        grace: timedelta = GRACE,
    ) -> None:
        self.store = store
        self.service = service
        self.active = active
        self.min_interval_minutes = min_interval_minutes
        self.clock = clock
        self.grace = grace
        self.log = stage_logger("schedule")

    def tick(self) -> None:
        state = self.store.state()
        if self.active and state.enabled:
            now = self.clock()
            minutes = self.store.minutes()
            for slot, due_at in due_slots(now, minutes, state.enabled_at, self.grace):
                if not self.store.claim(slot, due_at, now):
                    continue
                self._decide(slot)
        self.publish()

    def _decide(self, slot: str) -> None:
        run_id: str | None = None
        outcome: SlotOutcome
        try:
            run_id = self.service.start()
            outcome = "started"
        except RefreshConflict as error:
            outcome = (
                "skipped_running" if error.status_code == 409 else "skipped_blocked"
            )
        except CredentialError:
            outcome = "failed_to_start"
        except RuntimeError as error:
            outcome = (
                "skipped_disabled"
                if str(error) == "Manual refresh is disabled."
                else "failed_to_start"
            )
        except Exception:
            outcome = "failed_to_start"
        self.store.record(slot, outcome, run_id, self.clock())
        self.log.info("Scheduled slot %s Toronto time: %s.", slot, outcome)
        if run_id is not None:
            self.service.emit(
                f"Scheduled refresh for {slot[-5:]} Toronto time started."
            )

    def run_finished(self, outcome: RunOutcome) -> None:
        category = scheduled_failure(outcome)
        if category is None:
            return
        if self.store.fail_scheduled_run(outcome.run_id, category, self.clock()):
            self.log.warning(
                "Scheduled refresh failed (%s); scheduling turned off.", category
            )
            self.publish()

    def publish(self) -> None:
        state = self.store.state()
        upcoming = (
            next_run(self.clock(), self.store.minutes(), state.enabled_at)
            if self.active and state.enabled
            else None
        )
        self.service.set_schedule_notice(
            _utc_iso(upcoming),
            _utc_iso(state.failure.failed_at) if state.failure else None,
        )

    def view(self) -> RefreshScheduleView:
        state = self.store.state()
        minutes = self.store.minutes()
        upcoming = (
            next_run(self.clock(), minutes, state.enabled_at)
            if self.active and state.enabled
            else None
        )
        failure = state.failure
        return RefreshScheduleView(
            times=[format_minute(minute) for minute in minutes],
            enabled=state.enabled,
            server_enabled=self.active,
            next_run_at=_utc_iso(upcoming),
            min_interval_minutes=self.min_interval_minutes,
            max_times=MAX_TIMES,
            last=[
                ScheduleSlotView(
                    slot=entry.slot,
                    due_at=_utc_iso(entry.due_at) or "",
                    outcome=entry.outcome,
                    run_id=entry.run_id,
                    failure_category=entry.failure_category,
                )
                for entry in self.store.recent()
            ],
            failure=ScheduleFailureView(
                run_id=failure.run_id,
                slot=failure.slot,
                failed_at=_utc_iso(failure.failed_at) or "",
                category=failure.category,
            )
            if failure
            else None,
        )

    async def run(
        self,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        interval: float = TICK_SECONDS,
    ) -> None:
        """Tick forever off the event loop until cancelled at shutdown."""
        while True:
            try:
                await asyncio.to_thread(self.tick)
            except Exception:
                self.log.error("Scheduler tick failed; diagnostics suppressed.")
            await sleep(interval)
