"""Show-once API tokens: hashed storage, replacement, revocation, access recheck."""

import hashlib
import re
from pathlib import Path

import pytest

from waterlooworks_flash_next.api_tokens import (
    TOKEN_PATTERN,
    AgentIdentity,
    TokenStore,
    token_digest,
)
from waterlooworks_flash_next.approvals import ApprovalStore
from waterlooworks_flash_next.database import open_database
from waterlooworks_flash_next.request_limits import RequestLimiter


def _users(path: Path) -> None:
    connection = open_database(path)
    with connection:
        connection.execute("INSERT INTO app_users VALUES ('owner','admin',1)")
        connection.execute("INSERT INTO app_users VALUES ('member','member',2)")
    connection.close()
    ApprovalStore(path).approve("owner", "member")


def test_reset_returns_plaintext_once_and_stores_only_its_digest(
    tmp_path: Path,
) -> None:
    path = tmp_path / "db.sqlite3"
    _users(path)
    store = TokenStore(path)
    assert store.status("owner").configured is False
    issued = store.reset("owner", now=1_700_000_000)
    assert re.fullmatch(TOKEN_PATTERN, issued.token)
    assert issued.token.startswith("wwf_")
    assert issued.hint == f"wwf_…{issued.token[-4:]}"
    assert "token" not in repr(issued) or issued.token not in repr(issued)
    status = store.status("owner")
    assert status.configured is True
    assert status.hint == issued.hint
    assert status.created_at == "2023-11-14T22:13:20+00:00"
    assert status.last_used_at is None
    connection = open_database(path)
    rows = connection.execute("SELECT * FROM api_tokens").fetchall()
    connection.close()
    assert len(rows) == 1
    assert rows[0]["token_hash"] == hashlib.sha256(issued.token.encode()).hexdigest()
    assert issued.token not in "".join(str(value) for value in rows[0])
    identity = store.authenticate(issued.token, now=1_700_000_030)
    assert identity == AgentIdentity(uid="owner", role="admin")
    # The first use is recorded; a use inside the throttle window is not.
    assert store.status("owner").last_used_at == "2023-11-14T22:13:50+00:00"
    store.authenticate(issued.token, now=1_700_000_060)
    assert store.status("owner").last_used_at == "2023-11-14T22:13:50+00:00"
    store.authenticate(issued.token, now=1_700_000_120)
    assert store.status("owner").last_used_at == "2023-11-14T22:15:20+00:00"


def test_reset_replaces_and_revoke_invalidates_immediately(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    _users(path)
    store = TokenStore(path)
    first = store.reset("member", now=10)
    second = store.reset("member", now=20)
    assert first.token != second.token
    assert store.authenticate(first.token) is None
    assert store.authenticate(second.token) == AgentIdentity(
        uid="member", role="member"
    )
    assert store.revoke("member") is True
    assert store.revoke("member") is False
    assert store.authenticate(second.token) is None
    assert store.status("member").configured is False


def test_authenticate_rejects_malformed_unknown_and_tampered_tokens(
    tmp_path: Path,
) -> None:
    path = tmp_path / "db.sqlite3"
    _users(path)
    store = TokenStore(path)
    issued = store.reset("owner")
    for candidate in (
        None,
        "",
        "wwf_",
        issued.token[:-1],
        issued.token + "a",
        issued.token[:-1] + ("b" if issued.token[-1] != "b" else "c"),
        issued.token.upper(),
        "Bearer " + issued.token,
        token_digest(issued.token),
        "x" * 47,
    ):
        assert store.authenticate(candidate) is None
    assert store.status("owner").last_used_at is None


def test_token_dies_with_the_user_row(tmp_path: Path) -> None:
    # Membership rows are immutable; prove the recheck against a raw deletion.
    path = tmp_path / "db.sqlite3"
    _users(path)
    store = TokenStore(path)
    issued = store.reset("member")
    connection = open_database(path)
    with connection:
        connection.execute("DROP TRIGGER app_users_immutable_delete")
        connection.execute("DELETE FROM app_users WHERE uid='member'")
    assert connection.execute("SELECT count(*) FROM api_tokens").fetchone()[0] == 0
    connection.close()
    assert store.authenticate(issued.token) is None


@pytest.mark.parametrize("status", ["pending", "declined", "removed"])
def test_token_resolves_only_while_the_user_is_approved(
    tmp_path: Path, status: str
) -> None:
    path = tmp_path / "db.sqlite3"
    _users(path)
    store = TokenStore(path)
    issued = store.reset("member")
    assert store.authenticate(issued.token) is not None
    connection = open_database(path)
    with connection:
        connection.execute(
            "UPDATE user_approvals SET status=?, parent_uid=NULL, decided_by=?, "
            "decided_at=? WHERE uid='member'",
            (
                status,
                None if status == "pending" else "owner",
                None if status == "pending" else 3,
            ),
        )
    connection.close()
    assert store.authenticate(issued.token) is None
    assert store.status("member").configured is True  # the row itself remains


def test_request_limiter_is_per_key_and_reports_retry_after() -> None:
    limiter = RequestLimiter(limit=3, window_seconds=60)
    assert [limiter.admit("a", now=100.0) for _ in range(3)] == [None, None, None]
    assert limiter.admit("a", now=110.0) == 50
    assert limiter.admit("b", now=110.0) is None
    assert limiter.admit("a", now=159.9) == 1
    # A fresh window starts at the first admitted request after expiry.
    assert [limiter.admit("a", now=160.0) for _ in range(3)] == [None, None, None]
    assert limiter.admit("a", now=170.0) == 50
    with pytest.raises(ValueError):
        RequestLimiter(limit=0, window_seconds=60)


def test_request_limiter_bounds_tracked_keys() -> None:
    limiter = RequestLimiter(limit=1, window_seconds=10, max_keys=2)
    assert limiter.admit("a", now=0.0) is None
    assert limiter.admit("b", now=1.0) is None
    assert limiter.admit("c", now=2.0) is None
    assert limiter.tracked_keys <= 2
    # Expired windows are forgotten rather than retained forever.
    assert limiter.admit("d", now=100.0) is None
    assert limiter.tracked_keys == 1


def test_unknown_tokens_never_wait_on_the_write_lock(tmp_path: Path) -> None:
    # A flood of guesses must not queue behind (or block) real writers.
    import sqlite3

    path = tmp_path / "db.sqlite3"
    _users(path)
    store = TokenStore(path)
    issued = store.reset("member", now=1_700_000_000)
    assert store.authenticate(issued.token, now=1_700_000_000) is not None
    writer = sqlite3.connect(path, isolation_level=None)
    writer.execute("BEGIN IMMEDIATE")
    try:
        assert store.authenticate("wwf_" + "A" * 43) is None
        # A recently used token resolves without touching last_used_at.
        assert store.authenticate(issued.token, now=1_700_000_001) == AgentIdentity(
            uid="member", role="member"
        )
    finally:
        writer.execute("ROLLBACK")
        writer.close()
