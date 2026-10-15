"""Schema v22: the admission tree of app users (who approved whom).

Purely additive and dependency-free so ``database`` can import it eagerly.
``app_users`` stays immutable; admission state lives beside it.
"""

import sqlite3

APPROVAL_STATUSES = ("pending", "approved", "declined", "removed")


def migrate_user_approvals(connection: sqlite3.Connection) -> None:
    """Caller owns the transaction.

    One row per app user, created by trigger the moment the user row exists:
    the administrator is the approved root; everyone else starts ``pending``
    with no parent. ``approved`` rows hang under their approver
    (``parent_uid``); ``declined`` and ``removed`` rows keep who decided and
    when, and have no parent. The optional display fields are the profile
    snapshot taken from the verified Google token at sign-in, so approvers can
    recognise a pending person; they are never authority. Existing members are
    backfilled as pending: nobody is admitted retroactively.
    """
    statuses = ",".join(f"'{status}'" for status in APPROVAL_STATUSES)
    connection.execute(f"""
        CREATE TABLE user_approvals (
            uid TEXT PRIMARY KEY NOT NULL
                REFERENCES app_users(uid) ON DELETE CASCADE,
            status TEXT NOT NULL CHECK(status IN ({statuses})),
            parent_uid TEXT REFERENCES app_users(uid) ON DELETE SET NULL
                CHECK(parent_uid IS NULL OR parent_uid<>uid),
            decided_by TEXT REFERENCES app_users(uid) ON DELETE SET NULL
                CHECK(decided_by IS NULL OR decided_by<>uid),
            requested_at INTEGER NOT NULL CHECK(requested_at>0),
            decided_at INTEGER CHECK(decided_at IS NULL OR decided_at>0),
            display_name TEXT
                CHECK(display_name IS NULL OR length(display_name) BETWEEN 1 AND 320),
            email TEXT CHECK(email IS NULL OR length(email) BETWEEN 1 AND 320),
            photo_url TEXT
                CHECK(photo_url IS NULL OR length(photo_url) BETWEEN 1 AND 2048),
            CHECK(status<>'pending' OR (
                parent_uid IS NULL AND decided_by IS NULL AND decided_at IS NULL)),
            CHECK(status<>'approved' OR decided_at IS NOT NULL),
            CHECK(status NOT IN ('declined', 'removed') OR (
                parent_uid IS NULL AND decided_at IS NOT NULL))
        ) STRICT
    """)
    connection.execute(
        "CREATE INDEX idx_user_approvals_parent ON user_approvals(parent_uid)"
    )
    connection.execute(
        "CREATE INDEX idx_user_approvals_status ON user_approvals(status)"
    )
    # Every app user has exactly one admission row from the moment it exists.
    connection.execute("""
        CREATE TRIGGER user_approvals_for_new_user AFTER INSERT ON app_users
        BEGIN
            INSERT INTO user_approvals(uid, status, requested_at, decided_at)
            VALUES (
                NEW.uid,
                CASE NEW.role WHEN 'admin' THEN 'approved' ELSE 'pending' END,
                NEW.created_at,
                CASE NEW.role WHEN 'admin' THEN NEW.created_at ELSE NULL END
            );
        END
    """)
    # The administrator is the root of the tree: always approved, never nested.
    connection.execute("""
        CREATE TRIGGER user_approvals_admin_root BEFORE UPDATE ON user_approvals
        WHEN (SELECT role FROM app_users WHERE uid=NEW.uid)='admin'
            AND (NEW.status<>'approved' OR NEW.parent_uid IS NOT NULL)
        BEGIN SELECT RAISE(ABORT, 'Administrator admission is fixed'); END
    """)
    connection.execute("""
        INSERT INTO user_approvals(uid, status, requested_at, decided_at)
        SELECT uid,
            CASE role WHEN 'admin' THEN 'approved' ELSE 'pending' END,
            created_at,
            CASE role WHEN 'admin' THEN created_at ELSE NULL END
        FROM app_users
    """)
