"""Schema v17: admin-scheduled automatic refreshes (Toronto wall-clock times).

Purely additive. Dependency-free so ``database`` can import it eagerly like the
other migrations.
"""

import sqlite3

SLOT_OUTCOMES = (
    "claimed",
    "started",
    "skipped_running",
    "skipped_blocked",
    "skipped_disabled",
    "failed_to_start",
)
FAILURE_CATEGORIES = ("sign_in", "authentication", "rate_limit", "redirect", "ban_risk")


def _values(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


def migrate_refresh_schedule(connection: sqlite3.Connection) -> None:
    """Caller owns the transaction.

    ``refresh_schedule_slots`` is the single-fire ledger: its primary key (the
    Toronto local date plus HH:MM) makes claiming a slot one atomic INSERT, so
    restarts or several workers never fire the same slot twice. ``claimed`` is
    the transient outcome between the claim and the start decision. The
    singleton ``refresh_schedule_state`` row holds the admin toggle (off by
    default) and the scheduled-run failure that turned it off, as fixed
    categories only.
    """
    failures = _values(FAILURE_CATEGORIES)
    connection.execute("""
        CREATE TABLE refresh_schedule_times (
            minute_of_day INTEGER PRIMARY KEY NOT NULL
                CHECK(minute_of_day BETWEEN 0 AND 1439),
            created_at TEXT NOT NULL CHECK(datetime(created_at) IS NOT NULL)
        ) STRICT
    """)
    connection.execute(f"""
        CREATE TABLE refresh_schedule_slots (
            slot TEXT PRIMARY KEY NOT NULL
                CHECK(length(slot)=16 AND slot GLOB
                    '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]'),
            due_at TEXT NOT NULL CHECK(datetime(due_at) IS NOT NULL),
            outcome TEXT NOT NULL CHECK(outcome IN ({_values(SLOT_OUTCOMES)})),
            run_id TEXT REFERENCES collection_runs(id),
            decided_at TEXT NOT NULL CHECK(datetime(decided_at) IS NOT NULL),
            failure_category TEXT
                CHECK(failure_category IS NULL OR failure_category IN ({failures})),
            CHECK(run_id IS NULL OR outcome='started'),
            CHECK(failure_category IS NULL OR run_id IS NOT NULL)
        ) STRICT
    """)
    connection.execute(
        "CREATE INDEX idx_schedule_slots_due ON refresh_schedule_slots(due_at)"
    )
    connection.execute(
        "CREATE UNIQUE INDEX idx_schedule_slots_run ON refresh_schedule_slots(run_id) "
        "WHERE run_id IS NOT NULL"
    )
    connection.execute(f"""
        CREATE TABLE refresh_schedule_state (
            id INTEGER PRIMARY KEY NOT NULL CHECK(id=1),
            enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
            enabled_at TEXT
                CHECK(enabled_at IS NULL OR datetime(enabled_at) IS NOT NULL),
            disabled_reason TEXT CHECK(disabled_reason IS NULL OR
                disabled_reason IN ('admin','run_failed')),
            failed_run_id TEXT REFERENCES collection_runs(id),
            failed_slot TEXT,
            failed_at TEXT CHECK(failed_at IS NULL OR datetime(failed_at) IS NOT NULL),
            failure_category TEXT
                CHECK(failure_category IS NULL OR failure_category IN ({failures})),
            changed_at TEXT NOT NULL CHECK(datetime(changed_at) IS NOT NULL),
            CHECK((enabled=1) = (enabled_at IS NOT NULL)),
            CHECK((failed_run_id IS NULL) = (failed_at IS NULL)
                AND (failed_at IS NULL) = (failed_slot IS NULL)
                AND (failed_at IS NULL) = (failure_category IS NULL)),
            CHECK(failed_at IS NULL OR enabled=0)
        ) STRICT
    """)
    connection.execute(
        "INSERT INTO refresh_schedule_state(id, enabled, changed_at) "
        "VALUES(1, 0, strftime('%Y-%m-%dT%H:%M:%fZ','now'))"
    )
