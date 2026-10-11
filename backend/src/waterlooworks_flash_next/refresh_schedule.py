"""Admin-scheduled automatic refreshes in Toronto wall-clock time.

Times are stored as minutes of the Toronto day. A scheduled time becomes a
*slot* on each local date, keyed ``YYYY-MM-DD HH:MM``; a slot fires at most
once, ever, because claiming it is one INSERT on that primary key. DST: a
nonexistent spring-forward time fires once at the shifted instant (the
standard-time reading, 02:30 -> 03:30 EDT); a repeated fall-back time fires
once at its first occurrence.

Validation errors are fixed strings that never echo input.
"""

import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from waterlooworks_flash_next.database import open_database

TORONTO = ZoneInfo("America/Toronto")
MAX_TIMES = 24
GRACE = timedelta(minutes=10)
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
