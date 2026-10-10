"""Schema v15: per-run WaterlooWorks request accounting and clean pacing stops.

A separate one-row-per-run table keeps the migration purely additive (the
``collection_runs`` contract is unchanged). Dependency-free so ``database`` can
import it eagerly like the other migrations.
"""

import sqlite3

PACING_STOPS = ("request_cap", "daily_cap", "ban_risk")


def migrate_refresh_pacing(connection: sqlite3.Connection) -> None:
    """Caller owns the transaction; existing runs have no row (zero requests).

    ``ww_requests`` counts every WaterlooWorks request a run made (paced
    requests, retries and counted browser loads) for the rolling 24-hour budget.
    ``pacing_stop`` records why a run stopped early: a per-run or rolling cap
    (finished partial) or a ban-risk signal (failed and blocked).
    """
    stops = ",".join(f"'{value}'" for value in PACING_STOPS)
    connection.execute(f"""
        CREATE TABLE collection_run_pacing (
            run_id TEXT PRIMARY KEY NOT NULL REFERENCES collection_runs(id),
            ww_requests INTEGER NOT NULL DEFAULT 0 CHECK(ww_requests>=0),
            pacing_stop TEXT CHECK(pacing_stop IS NULL OR pacing_stop IN ({stops}))
        ) STRICT
    """)
