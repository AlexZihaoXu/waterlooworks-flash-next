"""Admission store on empty SQLite with structural UID rows; no provider data."""

import sqlite3
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from waterlooworks_flash_next.api_tokens import TokenStore
from waterlooworks_flash_next.approvals import (
    ApprovalConflict,
    ApprovalNotFound,
    ApprovalRefused,
    ApprovalStore,
)
from waterlooworks_flash_next.database import (
    StoredListing,
    open_database,
    save_listings,
    start_collection_run,
)
from waterlooworks_flash_next.discord_webhooks import WebhookStore
from waterlooworks_flash_next.posting_marks import MarkStore


def _database(path: Path, *members: str) -> None:
    connection = open_database(path)
    with connection:
        connection.execute("INSERT INTO app_users VALUES ('owner','admin',100)")
        for index, uid in enumerate(members):
            connection.execute(
                "INSERT INTO app_users VALUES (?,'member',?)", (uid, 200 + index)
            )
            connection.execute(
                "UPDATE user_approvals SET display_name=?, email=? WHERE uid=?",
                (f"Person {uid}", f"{uid}@example.invalid", uid),
            )
    connection.close()


def _status(path: Path, uid: str) -> tuple[object, ...]:
    with sqlite3.connect(path) as connection:
        return tuple(
            connection.execute(
                "SELECT status, parent_uid, decided_by, decided_at "
                "FROM user_approvals WHERE uid=?",
                (uid,),
            ).fetchone()
        )


def test_listing_shows_pending_people_and_only_names_of_own_approvees(
    tmp_path: Path,
) -> None:
    path = tmp_path / "db.sqlite3"
    _database(path, "zac", "devin")
    store = ApprovalStore(path)
    listing = store.listing("owner")
    assert [person.id for person in listing.pending] == ["zac", "devin"]
    assert listing.pending[0].model_dump() == {
        "id": "zac",
        "display_name": "Person zac",
        "email": "zac@example.invalid",
        "photo_url": None,
        "requested_at": "1970-01-01T00:03:20+00:00",
    }
    assert listing.approved_by_me == []
    assert store.pending_count() == 2
    store.approve("owner", "zac", now=300)
    store.approve("zac", "devin", now=400)
    assert store.pending_count() == 0
    assert [person.id for person in store.listing("owner").approved_by_me] == ["zac"]
    mine = store.listing("zac")
    assert mine.pending == []
    assert [person.model_dump() for person in mine.approved_by_me] == [
        {
            "id": "devin",
            "display_name": "Person devin",
            "photo_url": None,
            "approved_at": "1970-01-01T00:06:40+00:00",
        }
    ]
    assert "email" not in mine.approved_by_me[0].model_dump()
    assert _status(path, "devin") == ("approved", "zac", "zac", 400)


def test_decisions_require_a_pending_target_and_an_approved_actor(
    tmp_path: Path,
) -> None:
    path = tmp_path / "db.sqlite3"
    _database(path, "zac", "devin")
    store = ApprovalStore(path)
    with pytest.raises(ApprovalRefused):
        store.approve("zac", "devin")  # zac is still pending
    with pytest.raises(ApprovalRefused):
        store.approve("owner", "owner")
    with pytest.raises(ApprovalNotFound):
        store.approve("owner", "nobody")
    with pytest.raises(ApprovalNotFound):
        store.decline("owner", "nobody")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO app_sessions VALUES (?, 'devin', 9999999999)", ("a" * 64,)
        )
    store.decline("owner", "devin", now=300)
    assert _status(path, "devin") == ("declined", None, "owner", 300)
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute("SELECT count(*) FROM app_sessions").fetchone()[0] == 0
        )
    # Decided once: a second decision of either kind is a conflict.
    with pytest.raises(ApprovalConflict):
        store.approve("owner", "devin")
    with pytest.raises(ApprovalConflict):
        store.decline("owner", "devin")
    store.approve("owner", "zac", now=301)
    with pytest.raises(ApprovalRefused):
        store.decline("zac", "zac")


def test_remove_reparents_approvees_and_deletes_personal_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WW_CREDENTIALS_KEY", Fernet.generate_key().decode())
    path = tmp_path / "db.sqlite3"
    _database(path, "zac", "devin", "henry")
    store = ApprovalStore(path)
    store.approve("owner", "zac", now=300)
    store.approve("zac", "devin", now=301)
    store.approve("devin", "henry", now=302)
    # Structural per-user rows that must disappear with the user.
    connection = open_database(path)
    run_id = start_collection_run(connection)
    save_listings(connection, run_id, [StoredListing(id="1")])
    with connection:
        connection.execute(
            "INSERT INTO app_sessions VALUES (?, 'zac', 9999999999)", ("b" * 64,)
        )
    connection.close()
    token = TokenStore(path).reset("zac").token
    webhook = WebhookStore(path).add(
        "zac", "Mine", "https://discord.com/api/webhooks/123/" + "t" * 40
    )
    WebhookStore(path).mark_announced(webhook.id, ["1"], now=400)
    MarkStore(path).set_state("zac", "1", "applied")
    assert TokenStore(path).authenticate(token) is not None
    with pytest.raises(ApprovalRefused):
        store.remove("zac", "devin")  # not the administrator
    with pytest.raises(ApprovalRefused):
        store.remove("owner", "owner")
    with pytest.raises(ApprovalNotFound):
        store.remove("owner", "nobody")
    store.remove("owner", "zac", now=500)
    assert _status(path, "zac") == ("removed", None, "owner", 500)
    # Devin moves up under Zac's approver; Henry stays under Devin.
    assert _status(path, "devin") == ("approved", "owner", "zac", 301)
    assert _status(path, "henry") == ("approved", "devin", "devin", 302)
    assert TokenStore(path).authenticate(token) is None
    with sqlite3.connect(path) as connection:
        for table in (
            "app_sessions",
            "api_tokens",
            "discord_webhooks",
            "posting_marks",
        ):
            assert (
                connection.execute(
                    f"SELECT count(*) FROM {table} WHERE uid='zac'"
                ).fetchone()[0]
                == 0
            ), table
        assert (
            connection.execute(
                "SELECT count(*) FROM discord_webhook_announced"
            ).fetchone()[0]
            == 0
        )
    with pytest.raises(ApprovalConflict):
        store.remove("owner", "zac")  # already removed
    # Removing someone whose approver was already removed re-parents to the root.
    store.remove("owner", "devin", now=501)
    assert _status(path, "henry") == ("approved", "owner", "devin", 302)


def test_reinstate_returns_declined_or_removed_users_to_pending(
    tmp_path: Path,
) -> None:
    path = tmp_path / "db.sqlite3"
    _database(path, "zac", "devin")
    store = ApprovalStore(path)
    store.decline("owner", "zac", now=300)
    store.approve("owner", "devin", now=301)
    with pytest.raises(ApprovalConflict):
        store.reinstate("owner", "devin")  # approved, not out
    store.remove("owner", "devin", now=302)
    with pytest.raises(ApprovalRefused):
        store.reinstate("zac", "devin")
    with pytest.raises(ApprovalNotFound):
        store.reinstate("owner", "nobody")
    store.reinstate("owner", "zac", now=400)
    store.reinstate("owner", "devin", now=401)
    assert _status(path, "zac") == ("pending", None, None, None)
    assert _status(path, "devin") == ("pending", None, None, None)
    assert [person.id for person in store.listing("owner").pending] == [
        "zac",
        "devin",
    ]


def test_tree_nests_approvals_and_labels_who_decided(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    _database(path, "zac", "devin", "henry", "gone")
    store = ApprovalStore(path)
    assert store.tree().model_dump() == {
        "root": {
            "id": "owner",
            "display_name": None,
            "email": None,
            "photo_url": None,
            "role": "admin",
            "approved_at": "1970-01-01T00:01:40+00:00",
            "approved_by": None,
            "children": [],
        },
        "declined": [],
    }
    store.approve("owner", "zac", now=300)
    store.approve("zac", "devin", now=301)
    store.approve("zac", "henry", now=302)
    store.decline("owner", "gone", now=303)
    store.remove("owner", "henry", now=304)
    tree = store.tree()
    assert tree.root is not None
    assert [child.id for child in tree.root.children] == ["zac"]
    zac = tree.root.children[0]
    assert zac.approved_by is None  # the administrator has no snapshot here
    assert zac.approved_at == "1970-01-01T00:05:00+00:00"
    assert [child.model_dump() for child in zac.children] == [
        {
            "id": "devin",
            "display_name": "Person devin",
            "email": "devin@example.invalid",
            "photo_url": None,
            "role": "member",
            "approved_at": "1970-01-01T00:05:01+00:00",
            "approved_by": "Person zac",
            "children": [],
        }
    ]
    assert [
        (person.id, person.status, person.decided_by) for person in tree.declined
    ] == [
        ("henry", "removed", None),
        ("gone", "declined", None),
    ]
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE user_approvals SET display_name='Admin A' WHERE uid='owner'"
        )
    assert store.tree().declined[0].decided_by == "Admin A"
