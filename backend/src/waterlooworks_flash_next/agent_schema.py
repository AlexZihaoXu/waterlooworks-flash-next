"""Schema v18: show-once per-user API tokens and per-user posting marks.

Purely additive and dependency-free so ``database`` can import it eagerly.
"""

import sqlite3

POSTING_STATES = ("interested", "applied", "not_interested")


def migrate_agent_access(connection: sqlite3.Connection) -> None:
    """Caller owns the transaction.

    ``api_tokens`` keeps one active token per app user as a SHA-256 digest plus
    a display hint; the plaintext is never stored. ``posting_marks`` holds a
    user's optional state and optional viewed time for one saved posting; a row
    with neither is deleted rather than kept empty. Both tables follow the user
    row, so a removed user leaves no tokens or marks behind.
    """
    connection.execute("""
        CREATE TABLE api_tokens (
            uid TEXT PRIMARY KEY NOT NULL
                REFERENCES app_users(uid) ON DELETE CASCADE,
            token_hash TEXT NOT NULL UNIQUE
                CHECK(length(token_hash)=64 AND token_hash NOT GLOB '*[^0-9a-f]*'),
            hint TEXT NOT NULL CHECK(length(hint) BETWEEN 1 AND 16),
            created_at INTEGER NOT NULL CHECK(created_at>0),
            last_used_at INTEGER CHECK(last_used_at IS NULL OR last_used_at>=created_at)
        ) STRICT
    """)
    states = ",".join(f"'{state}'" for state in POSTING_STATES)
    connection.execute(f"""
        CREATE TABLE posting_marks (
            uid TEXT NOT NULL REFERENCES app_users(uid) ON DELETE CASCADE,
            posting_id TEXT NOT NULL
                REFERENCES postings(posting_id) ON DELETE CASCADE,
            state TEXT CHECK(state IS NULL OR state IN ({states})),
            viewed_at TEXT CHECK(viewed_at IS NULL OR datetime(viewed_at) IS NOT NULL),
            updated_at TEXT NOT NULL CHECK(datetime(updated_at) IS NOT NULL),
            PRIMARY KEY(uid, posting_id),
            CHECK(state IS NOT NULL OR viewed_at IS NOT NULL)
        ) STRICT
    """)
    connection.execute(
        "CREATE INDEX idx_posting_marks_posting ON posting_marks(posting_id)"
    )
