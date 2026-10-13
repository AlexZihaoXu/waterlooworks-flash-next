"""A saved-data generation counter for cache invalidation, maintained by triggers.

Every insert, update or delete on a saved-data table bumps one counter row, so
in-memory snapshots stay exact whichever process writes (the API, CLIs or the
Node extraction workers). Auth, credential, ledger, lease and attempt tables are
excluded: sign-ins, logouts and progress heartbeats never invalidate snapshots.
Cached payload readers must therefore never depend on an excluded table alone.

The triggers are cache plumbing, not part of the versioned data contract. They
are (re)created idempotently after migrations on every ``open_database``, and
readers fall back to ``PRAGMA data_version`` (any commit invalidates) whenever
the trigger set is incomplete, e.g. after a migration rebuilt a table.
"""

import sqlite3

TABLE = "data_generation"
PREFIX = "data_generation_"
OPERATIONS = ("insert", "update", "delete")

EXCLUDED_TABLES = frozenset(
    {
        # Authentication, sessions and secrets.
        "api_tokens",
        "app_sessions",
        "app_users",
        # Per-user encrypted Discord webhook destinations and send bookkeeping.
        "discord_webhooks",
        "discord_webhook_announced",
        # Per-user posting marks never enter the identity-free shared snapshots.
        "posting_marks",
        "openai_reauth_challenges",
        "openai_credentials",
        "waterlooworks_credentials",
        # Usage ledger, run/lease bookkeeping and per-attempt checkpoints.
        "openai_runs",
        "openai_attempts",
        "collection_runs",
        "posting_content_attempts",
        "posting_fetch_attempts",
        "inference_jobs",
        "inference_job_items",
        "source_extraction_runs",
        "country_inference_runs",
    }
)


def _expected(tables: list[str]) -> set[str]:
    return {
        f"{PREFIX}{table}_{operation}"
        for table in tables
        if table not in EXCLUDED_TABLES and table != TABLE
        for operation in OPERATIONS
    }


def _schema(connection: sqlite3.Connection) -> tuple[list[str], set[str]]:
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT GLOB 'sqlite_*' ORDER BY name"
        )
    ]
    triggers = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name GLOB ?",
            (PREFIX + "*",),
        )
    }
    return tables, triggers


def ensure_data_generation(connection: sqlite3.Connection) -> None:
    """Create the counter and any missing triggers; a no-op when already exact."""
    tables, triggers = _schema(connection)
    if TABLE in tables and triggers == _expected(tables):
        return
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            f"CREATE TABLE IF NOT EXISTS {TABLE} ("
            "singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
            "value INTEGER NOT NULL CHECK(value>=0)) STRICT"
        )
        connection.execute(
            f"INSERT OR IGNORE INTO {TABLE}(singleton,value) VALUES(1,0)"
        )
        tables, triggers = _schema(connection)
        expected = _expected(tables)
        for name in sorted(triggers - expected):
            if name.replace("_", "").isalnum():
                connection.execute(f"DROP TRIGGER {name}")
        for table in tables:
            if f"{PREFIX}{table}_insert" not in expected:
                continue
            if not table.replace("_", "").isalnum():
                raise ValueError("Unsupported table identifier.")
            for operation in OPERATIONS:
                connection.execute(
                    f"CREATE TRIGGER IF NOT EXISTS {PREFIX}{table}_{operation} "
                    f"AFTER {operation.upper()} ON {table} BEGIN "
                    f"UPDATE {TABLE} SET value=value+1 WHERE singleton=1; END"
                )


def read_generation(connection: sqlite3.Connection) -> tuple[str, int]:
    """Cache key: the trigger counter when exact, else any-commit data_version.

    One statement reads the counter and the schema from the same snapshot.
    """
    try:
        row = connection.execute(
            f"SELECT (SELECT value FROM {TABLE} WHERE singleton=1),"
            "(SELECT group_concat(name,char(31)) FROM sqlite_master "
            "WHERE type='table' AND name NOT GLOB 'sqlite_*'),"
            "(SELECT group_concat(name,char(31)) FROM sqlite_master "
            "WHERE type='trigger' AND name GLOB ?)",
            (PREFIX + "*",),
        ).fetchone()
    except sqlite3.DatabaseError:
        row = None
    if row is not None and row[0] is not None:
        tables = row[1].split("\x1f") if row[1] else []
        triggers = set(row[2].split("\x1f")) if row[2] else set()
        if triggers == _expected(tables):
            return ("generation", int(row[0]))
    return (
        "data_version",
        int(connection.execute("PRAGMA data_version").fetchone()[0]),
    )
