"""Schema v19: per-user Discord webhook destinations for new job alerts.

Purely additive and dependency-free so ``database`` can import it eagerly.
"""

import sqlite3

WEBHOOK_STATUSES = ("ok", "failed", "gone")


def migrate_discord_webhooks(connection: sqlite3.Connection) -> None:
    """Caller owns the transaction.

    Webhook URLs are bearer secrets: only Fernet ciphertext (purpose-bound to
    the owning user) and a short non-secret hint derived from the webhook ID
    are stored. Rows follow the user row, so a removed user leaves none behind.
    """
    statuses = ",".join(f"'{status}'" for status in WEBHOOK_STATUSES)
    connection.execute(f"""
        CREATE TABLE discord_webhooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL REFERENCES app_users(uid) ON DELETE CASCADE,
            label TEXT NOT NULL CHECK(length(label) BETWEEN 1 AND 60),
            ciphertext BLOB NOT NULL
                CHECK(length(ciphertext) BETWEEN 100 AND 4000),
            hint TEXT NOT NULL CHECK(length(hint) BETWEEN 1 AND 16),
            enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
            created_at INTEGER NOT NULL CHECK(created_at>0),
            last_sent_at INTEGER CHECK(last_sent_at IS NULL OR last_sent_at>0),
            last_status TEXT
                CHECK(last_status IS NULL OR last_status IN ({statuses})),
            last_error_at INTEGER CHECK(last_error_at IS NULL OR last_error_at>0)
        ) STRICT
    """)
    connection.execute("CREATE INDEX idx_discord_webhooks_uid ON discord_webhooks(uid)")
