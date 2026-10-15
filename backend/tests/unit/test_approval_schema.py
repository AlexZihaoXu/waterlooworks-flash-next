"""Schema v22 admission rows: structural UID rows only, no profile or source data."""

import sqlite3
from pathlib import Path

import pytest

from waterlooworks_flash_next import database
from waterlooworks_flash_next.database import open_database


def _rows(path: Path) -> dict[str, tuple[object, ...]]:
    with sqlite3.connect(path) as connection:
        return {
            row[0]: tuple(row[1:])
            for row in connection.execute(
                "SELECT uid, status, parent_uid, decided_by, requested_at, "
                "decided_at, display_name, email, photo_url FROM user_approvals"
            )
        }


def test_upgrade_marks_existing_admin_approved_and_members_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "db.sqlite3"
    with monkeypatch.context() as previous:
        previous.setattr(database, "SCHEMA_VERSION", 21)
        connection = open_database(path)
        with connection:
            connection.execute("INSERT INTO app_users VALUES ('owner','admin',100)")
            connection.execute("INSERT INTO app_users VALUES ('m1','member',200)")
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 21
        connection.close()
    connection = open_database(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 22
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()
    assert _rows(path) == {
        "owner": ("approved", None, None, 100, 100, None, None, None),
        "m1": ("pending", None, None, 200, None, None, None, None),
    }


def test_every_new_app_user_gets_an_admission_row(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "db.sqlite3")
    with connection:
        connection.execute("INSERT INTO app_users VALUES ('owner','admin',5)")
        connection.execute("INSERT INTO app_users VALUES ('m1','member',6)")
    assert _rows(tmp_path / "db.sqlite3") == {
        "owner": ("approved", None, None, 5, 5, None, None, None),
        "m1": ("pending", None, None, 6, None, None, None, None),
    }
    connection.close()


def test_admission_invariants_are_enforced_by_the_schema(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    connection = open_database(path)
    with connection:
        connection.execute("INSERT INTO app_users VALUES ('owner','admin',5)")
        connection.execute("INSERT INTO app_users VALUES ('m1','member',6)")
    rejected = (
        # The administrator is the fixed root: never pending, declined or nested.
        "UPDATE user_approvals SET status='pending' WHERE uid='owner'",
        "UPDATE user_approvals SET status='declined', decided_at=7 WHERE uid='owner'",
        "UPDATE user_approvals SET parent_uid='m1' WHERE uid='owner'",
        # Nobody approves themselves; pending rows carry no decision.
        "UPDATE user_approvals SET status='approved', parent_uid='m1', "
        "decided_at=7 WHERE uid='m1'",
        "UPDATE user_approvals SET parent_uid='owner' WHERE uid='m1'",
        "UPDATE user_approvals SET decided_at=7 WHERE uid='m1'",
        # Approvals and refusals record when they happened.
        "UPDATE user_approvals SET status='approved' WHERE uid='m1'",
        "UPDATE user_approvals SET status='declined' WHERE uid='m1'",
        # Unknown statuses and unknown users are impossible.
        "UPDATE user_approvals SET status='banned' WHERE uid='m1'",
        "INSERT INTO user_approvals(uid,status,requested_at) VALUES ('x','pending',1)",
        "DELETE FROM app_users WHERE uid='m1'",
    )
    for sql in rejected:
        with pytest.raises(sqlite3.IntegrityError), connection:
            connection.execute(sql)
    with connection:
        connection.execute(
            "UPDATE user_approvals SET status='approved', parent_uid='owner', "
            "decided_by='owner', decided_at=7 WHERE uid='m1'"
        )
    assert _rows(path)["m1"] == ("approved", "owner", "owner", 6, 7, None, None, None)
    connection.close()
