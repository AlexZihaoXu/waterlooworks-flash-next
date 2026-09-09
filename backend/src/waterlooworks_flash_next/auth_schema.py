"""Additive app authentication schema; unrelated saved-source tables are untouched."""

import sqlite3


def migrate_app_auth(connection: sqlite3.Connection) -> None:
    connection.execute("""
        CREATE TABLE app_users (
            uid TEXT PRIMARY KEY NOT NULL CHECK(length(uid) BETWEEN 1 AND 128),
            role TEXT NOT NULL CHECK(role IN ('admin', 'member')),
            created_at INTEGER NOT NULL
        ) STRICT
    """)
    connection.execute(
        "CREATE UNIQUE INDEX app_single_admin ON app_users(role) WHERE role='admin'"
    )
    # A missing/deleted admin must never silently reopen bootstrap.
    for operation in ("UPDATE", "DELETE"):
        connection.execute(f"""
            CREATE TRIGGER app_users_immutable_{operation.lower()}
            BEFORE {operation} ON app_users
            BEGIN SELECT RAISE(ABORT, 'App membership is immutable'); END
        """)
    connection.execute("""
        CREATE TABLE app_sessions (
            token_hash TEXT PRIMARY KEY NOT NULL CHECK(length(token_hash)=64),
            uid TEXT REFERENCES app_users(uid) ON DELETE RESTRICT,
            expires_at INTEGER NOT NULL
        ) STRICT
    """)
    connection.execute("CREATE INDEX app_session_expiry ON app_sessions(expires_at)")
