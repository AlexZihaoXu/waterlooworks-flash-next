"""Per-user posting marks: an optional state and an optional viewed time.

Marks are private annotations keyed by app user and saved posting. They are
never part of the shared workspace snapshot and never leave the owning user.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from waterlooworks_flash_next.agent_schema import POSTING_STATES

PostingState = Literal["interested", "applied", "not_interested"]
assert set(get_args(PostingState)) == set(POSTING_STATES)


class UnknownPosting(LookupError):
    """The posting ID is not saved; fixed message, never the submitted value."""


class PostingMark(BaseModel):
    """One user's annotations on one saved posting."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    posting_id: str
    state: PostingState | None = Field(
        description="interested, applied or not_interested; null when unmarked."
    )
    viewed_at: str | None = Field(
        description="UTC ISO 8601 of the first explicit view mark; null if unviewed."
    )
    updated_at: str | None = Field(
        description="UTC ISO 8601 of the latest change; null when nothing is stored."
    )


def _iso(now: str | None) -> str:
    instant = (
        datetime.now(UTC)
        if now is None
        else datetime.fromisoformat(now.replace("Z", "+00:00")).astimezone(UTC)
    )
    return instant.isoformat()


class MarkStore:
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

    def read_all(self, uid: str) -> dict[str, PostingMark]:
        connection = self._connect(readonly=True)
        try:
            return {
                row["posting_id"]: PostingMark(**dict(row))
                for row in connection.execute(
                    "SELECT posting_id, state, viewed_at, updated_at "
                    "FROM posting_marks WHERE uid=?",
                    (uid,),
                )
            }
        finally:
            connection.close()

    def set_state(
        self,
        uid: str,
        posting_id: str,
        state: PostingState | None,
        *,
        now: str | None = None,
    ) -> PostingMark:
        if state is not None and state not in POSTING_STATES:
            raise ValueError("Unknown posting state.")
        return self._write(uid, posting_id, "state", state, _iso(now))

    def set_viewed(
        self, uid: str, posting_id: str, viewed: bool, *, now: str | None = None
    ) -> PostingMark:
        return self._write(uid, posting_id, "viewed", viewed, _iso(now))

    def _write(
        self,
        uid: str,
        posting_id: str,
        column: Literal["state", "viewed"],
        value: str | bool | None,
        at: str,
    ) -> PostingMark:
        connection = self._connect()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                if not connection.execute(
                    "SELECT 1 FROM postings WHERE posting_id=?", (posting_id,)
                ).fetchone():
                    raise UnknownPosting("Stored posting not found.")
                row = connection.execute(
                    "SELECT state, viewed_at FROM posting_marks "
                    "WHERE uid=? AND posting_id=?",
                    (uid, posting_id),
                ).fetchone()
                state = row["state"] if row else None
                viewed_at = row["viewed_at"] if row else None
                if column == "state":
                    state = value if isinstance(value, str) else None
                elif value:
                    viewed_at = viewed_at or at
                else:
                    viewed_at = None
                if state is None and viewed_at is None:
                    connection.execute(
                        "DELETE FROM posting_marks WHERE uid=? AND posting_id=?",
                        (uid, posting_id),
                    )
                    return PostingMark(
                        posting_id=posting_id,
                        state=None,
                        viewed_at=None,
                        updated_at=None,
                    )
                connection.execute(
                    "INSERT INTO posting_marks(uid, posting_id, state, viewed_at, "
                    "updated_at) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(uid, posting_id) DO UPDATE SET state=excluded.state, "
                    "viewed_at=excluded.viewed_at, updated_at=excluded.updated_at",
                    (uid, posting_id, state, viewed_at, at),
                )
                return PostingMark(
                    posting_id=posting_id,
                    state=state,
                    viewed_at=viewed_at,
                    updated_at=at,
                )
        finally:
            connection.close()
