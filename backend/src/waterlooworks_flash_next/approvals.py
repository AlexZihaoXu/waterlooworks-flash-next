"""Admission tree: approve, decline, remove and reinstate app users.

Every app user has one ``user_approvals`` row (schema v22). Any approved user
may approve or decline a pending user, who then hangs under the approver.
Only the administrator (the fixed root) removes an approved user: the removed
user's own approvees move up under the removed user's approver, and their
sessions, agent token, Discord webhooks and posting marks are deleted. A
declined or removed identity stays out until the administrator moves it back
to pending. Nothing here reads tokens or contacts a provider.
"""

import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ApprovalStatus = Literal["pending", "approved", "declined", "removed"]


class ApprovalNotFound(LookupError):
    """No such app user."""


class ApprovalConflict(Exception):
    """The user is not in the state the action requires (e.g. already decided)."""


class ApprovalRefused(Exception):
    """Acting on oneself or on the administrator is never allowed."""


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class PendingUser(_Contract):
    """What an approver may see about someone waiting: the sign-in snapshot."""

    id: str = Field(description="The pending user's app identifier.")
    display_name: str | None
    email: str | None = Field(description="Google-verified address, if any.")
    photo_url: str | None
    requested_at: str = Field(description="UTC ISO 8601 of the first sign-in.")


class ApprovedUser(_Contract):
    """Someone the caller approved: name and photo only, never the email."""

    id: str
    display_name: str | None
    photo_url: str | None
    approved_at: str


class ApprovalListing(_Contract):
    pending: list[PendingUser] = Field(
        description="Everyone waiting, oldest request first."
    )
    approved_by_me: list[ApprovedUser] = Field(
        description="People the caller approved who still have access."
    )


class TreeUser(_Contract):
    """One approved user and, nested, everyone they approved (admin view)."""

    id: str
    display_name: str | None
    email: str | None
    photo_url: str | None
    role: Literal["admin", "member"]
    approved_at: str | None = Field(
        description="UTC ISO 8601; the administrator's bootstrap time for the root."
    )
    approved_by: str | None = Field(
        description="Display label of the approver (name, else email); null for root."
    )
    children: list["TreeUser"]


class DeclinedUser(_Contract):
    id: str
    display_name: str | None
    email: str | None
    photo_url: str | None
    status: Literal["declined", "removed"]
    decided_at: str
    decided_by: str | None = Field(
        description="Display label of who declined or removed them."
    )


class ApprovalTree(_Contract):
    root: TreeUser | None = Field(
        description="The administrator with everyone approved beneath them."
    )
    declined: list[DeclinedUser] = Field(
        description="Declined and removed users, most recent decision first."
    )


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def _label(row: sqlite3.Row | None) -> str | None:
    if row is None:
        return None
    label = row["display_name"] or row["email"]
    return str(label) if label else None


class ApprovalStore:
    """Short-lived connections per call; the database is already migrated."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self, *, readonly: bool = False) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path.resolve().as_uri() + ("?mode=ro" if readonly else "?mode=rw"),
            uri=True,
            timeout=5,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        if readonly:
            connection.execute("PRAGMA query_only=ON")
        return connection

    def listing(self, uid: str) -> ApprovalListing:
        connection = self._connect(readonly=True)
        try:
            pending = connection.execute(
                "SELECT uid, display_name, email, photo_url, requested_at "
                "FROM user_approvals WHERE status='pending' "
                "ORDER BY requested_at, uid"
            ).fetchall()
            mine = connection.execute(
                "SELECT uid, display_name, photo_url, decided_at "
                "FROM user_approvals WHERE status='approved' AND parent_uid=? "
                "ORDER BY decided_at, uid",
                (uid,),
            ).fetchall()
        finally:
            connection.close()
        return ApprovalListing(
            pending=[
                PendingUser(
                    id=row["uid"],
                    display_name=row["display_name"],
                    email=row["email"],
                    photo_url=row["photo_url"],
                    requested_at=_iso(row["requested_at"]),
                )
                for row in pending
            ],
            approved_by_me=[
                ApprovedUser(
                    id=row["uid"],
                    display_name=row["display_name"],
                    photo_url=row["photo_url"],
                    approved_at=_iso(row["decided_at"]),
                )
                for row in mine
            ],
        )

    def pending_count(self) -> int:
        connection = self._connect(readonly=True)
        try:
            return int(
                connection.execute(
                    "SELECT count(*) FROM user_approvals WHERE status='pending'"
                ).fetchone()[0]
            )
        finally:
            connection.close()

    def approve(self, actor: str, target: str, *, now: int | None = None) -> None:
        """The pending ``target`` becomes approved under ``actor``."""
        self._decide(actor, target, "approved", now)

    def decline(self, actor: str, target: str, *, now: int | None = None) -> None:
        """The pending ``target`` is declined; its waiting sessions end now."""
        self._decide(actor, target, "declined", now)

    def _decide(
        self,
        actor: str,
        target: str,
        status: Literal["approved", "declined"],
        now: int | None,
    ) -> None:
        now = int(time.time()) if now is None else now
        if actor == target:
            raise ApprovalRefused("Nobody approves or declines themselves.")
        connection = self._connect()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                acting = connection.execute(
                    "SELECT status FROM user_approvals WHERE uid=?", (actor,)
                ).fetchone()
                if acting is None or acting["status"] != "approved":
                    raise ApprovalRefused("Only approved users decide admissions.")
                current = connection.execute(
                    "SELECT status FROM user_approvals WHERE uid=?", (target,)
                ).fetchone()
                if current is None:
                    raise ApprovalNotFound("User not found.")
                if current["status"] != "pending":
                    raise ApprovalConflict("That request was already decided.")
                connection.execute(
                    "UPDATE user_approvals SET status=?, parent_uid=?, decided_by=?, "
                    "decided_at=? WHERE uid=? AND status='pending'",
                    (
                        status,
                        actor if status == "approved" else None,
                        actor,
                        now,
                        target,
                    ),
                )
                if status == "declined":
                    connection.execute(
                        "DELETE FROM app_sessions WHERE uid=?", (target,)
                    )
        finally:
            connection.close()

    def remove(self, admin: str, target: str, *, now: int | None = None) -> None:
        """Administrator only: the approved ``target`` loses access now.

        Their approvees move up under the removed user's own approver, and
        their sessions, agent token, Discord webhooks (with the announced
        bookkeeping) and posting marks are deleted. The admission row stays
        as ``removed`` with who removed them and when.
        """
        now = int(time.time()) if now is None else now
        if admin == target:
            raise ApprovalRefused("The administrator cannot be removed.")
        connection = self._connect()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                acting = connection.execute(
                    "SELECT role FROM app_users WHERE uid=?", (admin,)
                ).fetchone()
                if acting is None or acting["role"] != "admin":
                    raise ApprovalRefused("Only the administrator removes users.")
                current = connection.execute(
                    "SELECT a.status, a.parent_uid, u.role FROM user_approvals a "
                    "JOIN app_users u ON u.uid=a.uid WHERE a.uid=?",
                    (target,),
                ).fetchone()
                if current is None:
                    raise ApprovalNotFound("User not found.")
                if current["role"] == "admin":
                    raise ApprovalRefused("The administrator cannot be removed.")
                if current["status"] != "approved":
                    raise ApprovalConflict("Only an approved user can be removed.")
                connection.execute(
                    "UPDATE user_approvals SET parent_uid=? WHERE parent_uid=?",
                    (current["parent_uid"] or admin, target),
                )
                connection.execute(
                    "UPDATE user_approvals SET status='removed', parent_uid=NULL, "
                    "decided_by=?, decided_at=? WHERE uid=?",
                    (admin, now, target),
                )
                for table in (
                    "app_sessions",
                    "api_tokens",
                    "discord_webhooks",
                    "posting_marks",
                ):
                    connection.execute(f"DELETE FROM {table} WHERE uid=?", (target,))
        finally:
            connection.close()

    def reinstate(self, admin: str, target: str, *, now: int | None = None) -> None:
        """Administrator only: a declined or removed user waits again."""
        now = int(time.time()) if now is None else now
        connection = self._connect()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                acting = connection.execute(
                    "SELECT role FROM app_users WHERE uid=?", (admin,)
                ).fetchone()
                if acting is None or acting["role"] != "admin":
                    raise ApprovalRefused("Only the administrator reinstates users.")
                current = connection.execute(
                    "SELECT status FROM user_approvals WHERE uid=?", (target,)
                ).fetchone()
                if current is None:
                    raise ApprovalNotFound("User not found.")
                if current["status"] not in ("declined", "removed"):
                    raise ApprovalConflict("Only a declined or removed user waits.")
                connection.execute(
                    "UPDATE user_approvals SET status='pending', parent_uid=NULL, "
                    "decided_by=NULL, decided_at=NULL, requested_at=? WHERE uid=?",
                    (now, target),
                )
        finally:
            connection.close()

    def tree(self) -> ApprovalTree:
        """The whole tree plus declined/removed users: administrator view."""
        connection = self._connect(readonly=True)
        try:
            rows = connection.execute(
                "SELECT a.uid, a.status, a.parent_uid, a.decided_by, a.decided_at, "
                "a.display_name, a.email, a.photo_url, u.role "
                "FROM user_approvals a JOIN app_users u ON u.uid=a.uid "
                "ORDER BY a.decided_at, a.uid"
            ).fetchall()
        finally:
            connection.close()
        by_uid = {row["uid"]: row for row in rows}
        root_row = next((row for row in rows if row["role"] == "admin"), None)
        children: dict[str | None, list[sqlite3.Row]] = {}
        for row in rows:
            if row["status"] == "approved" and row["role"] != "admin":
                # An approved member without a parent is unexpected; show it
                # under the root rather than hide it.
                parent = row["parent_uid"] or (root_row["uid"] if root_row else None)
                children.setdefault(parent, []).append(row)

        def node(row: sqlite3.Row, seen: frozenset[str]) -> TreeUser:
            return TreeUser(
                id=row["uid"],
                display_name=row["display_name"],
                email=row["email"],
                photo_url=row["photo_url"],
                role=row["role"],
                approved_at=_iso(row["decided_at"])
                if row["decided_at"] is not None
                else None,
                approved_by=_label(by_uid.get(row["decided_by"]))
                if row["decided_by"] is not None
                else None,
                children=[
                    node(child, seen | {child["uid"]})
                    for child in children.get(row["uid"], [])
                    if child["uid"] not in seen
                ],
            )

        return ApprovalTree(
            root=node(root_row, frozenset({root_row["uid"]})) if root_row else None,
            declined=[
                DeclinedUser(
                    id=row["uid"],
                    display_name=row["display_name"],
                    email=row["email"],
                    photo_url=row["photo_url"],
                    status=row["status"],
                    decided_at=_iso(row["decided_at"]),
                    decided_by=_label(by_uid.get(row["decided_by"]))
                    if row["decided_by"] is not None
                    else None,
                )
                for row in sorted(
                    (row for row in rows if row["status"] in ("declined", "removed")),
                    key=lambda row: (-row["decided_at"], row["uid"]),
                )
            ],
        )
