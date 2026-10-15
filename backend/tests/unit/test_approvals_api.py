"""Admission routes and the pending-user lockout, on real sessions and SQLite.

Real hashed sessions, roles, admission rows and the transport/CSRF
middleware run; only Firebase readiness is overridden. Users are structural
UID rows with generic display snapshots; no provider data is invented.
"""

import sqlite3
import time
from collections.abc import Iterable, Iterator
from pathlib import Path

from auth_support import TEST_OWNER, approve_member, member_cookie, session_client
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from waterlooworks_flash_next.agent_api import AGENT_PREFIX
from waterlooworks_flash_next.api import create_app
from waterlooworks_flash_next.app_auth import COOKIE
from waterlooworks_flash_next.approvals import ApprovalStore
from waterlooworks_flash_next.approvals_api import (
    ADMIN_ROUTE,
    DECISION_LIMIT,
    ROUTE,
)

# Everything a signed-in but not yet approved user may still reach: their own
# session state, their own profile, sign-out and the public agent documents.
PENDING_ALLOWED = {
    ("/api/health", "GET"),
    ("/api/auth/prepare", "POST"),
    ("/api/auth/exchange", "POST"),
    ("/api/auth/session", "GET"),
    ("/api/auth/profile", "GET"),
    ("/api/auth/logout", "POST"),
    (f"{AGENT_PREFIX}openapi.json", "GET"),
    (f"{AGENT_PREFIX}guide", "GET"),
}
PATH_VALUES = {"posting_id": "1", "webhook_id": "1", "uid": "someone", "slot": "1"}


def _flatten(routes: Iterable[object]) -> Iterator[tuple[str, str]]:
    for route in routes:
        if isinstance(route, APIRoute):
            for method in route.methods or set():
                yield route.path, method
        nested = getattr(route, "original_router", None)
        if nested is not None:
            yield from _flatten(nested.routes)


def _use(client: TestClient, cookie: str) -> None:
    client.cookies.clear()
    client.cookies.set(COOKIE, cookie, domain="localhost.local", path="/api")


def _snapshot(
    path: Path, uid: str, name: str, email: str | None, requested_at: int
) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE user_approvals SET display_name=?, email=?, photo_url=?, "
            "requested_at=? WHERE uid=?",
            (
                name,
                email,
                "https://lh3.googleusercontent.com/a/structural",
                requested_at,
                uid,
            ),
        )


def test_pending_user_is_locked_out_of_every_route_except_its_own_session(
    tmp_path: Path,
) -> None:
    path = tmp_path / "db.sqlite3"
    app = create_app(path)
    with session_client(path, app=app) as client:
        pending = member_cookie(path, "pending-uid", approved=False)
        _use(client, pending)
        session = client.get("/api/auth/session")
        assert session.status_code == 200
        assert session.json()["approval"] == "pending"
        assert session.json()["role"] == "member"
        registered = {
            (route, method)
            for route, method in _flatten(app.routes)
            if method not in {"HEAD", "OPTIONS"}
        }
        assert len(registered) > 40
        checked = 0
        for route, method in sorted(registered):
            if (route, method) in PENDING_ALLOWED:
                continue
            url = route
            for name, value in PATH_VALUES.items():
                url = url.replace(f"{{{name}}}", value)
            assert "{" not in url, route
            if route.startswith(AGENT_PREFIX):
                # Bearer routes ignore cookies: no token can exist for a
                # pending user, and the account route to mint one is refused.
                response = client.request(method, url, json={})
                assert response.status_code == 401, (route, method)
                continue
            response = client.request(method, url, json={})
            assert response.status_code == 403, (route, method, response.text)
            assert response.json() == {"detail": "Approval required."}
            assert response.headers["cache-control"] == "no-store"
            checked += 1
        assert checked >= 30
        # Sign-out still works for a pending user and ends the session.
        assert client.post("/api/auth/logout", json={}).status_code == 200
        assert client.get("/api/auth/session").status_code == 401


def test_approved_users_see_pending_people_and_admit_or_decline_them(
    tmp_path: Path,
) -> None:
    path = tmp_path / "db.sqlite3"
    with session_client(path) as client:
        admin = client.cookies.get(COOKIE)
        assert admin is not None
        zac = member_cookie(path, "zac", approved=False)
        _snapshot(path, "zac", "Person Z", "zac@example.invalid", 100)
        devin = member_cookie(path, "devin", approved=False)
        _snapshot(path, "devin", "Person D", None, 200)
        # Pending users cannot read or act on the list at all.
        _use(client, zac)
        assert client.get(ROUTE).status_code == 403
        assert client.post(f"{ROUTE}/devin/approve", json={}).status_code == 403
        _use(client, admin)
        listing = client.get(ROUTE)
        assert listing.status_code == 200
        assert listing.headers["cache-control"] == "no-store"
        body = listing.json()
        assert [person["id"] for person in body["pending"]] == ["zac", "devin"]
        assert body["pending"][0] == {
            "id": "zac",
            "display_name": "Person Z",
            "email": "zac@example.invalid",
            "photo_url": "https://lh3.googleusercontent.com/a/structural",
            "requested_at": body["pending"][0]["requested_at"],
        }
        assert body["approved_by_me"] == []
        approved = client.post(f"{ROUTE}/zac/approve", json={})
        assert approved.status_code == 200
        assert approved.headers["cache-control"] == "no-store"
        assert [person["id"] for person in approved.json()["pending"]] == ["devin"]
        assert approved.json()["approved_by_me"] == [
            {
                "id": "zac",
                "display_name": "Person Z",
                "photo_url": "https://lh3.googleusercontent.com/a/structural",
                "approved_at": approved.json()["approved_by_me"][0]["approved_at"],
            }
        ]
        # Zac's existing session now has full access, and Zac may decide too.
        _use(client, zac)
        assert client.get("/api/auth/session").json()["approval"] == "approved"
        assert client.get("/api/workspace").status_code == 200
        declined = client.post(f"{ROUTE}/devin/decline", json={})
        assert declined.status_code == 200
        assert declined.json() == {"pending": [], "approved_by_me": []}
        # Devin's waiting session ended; signing in again is refused.
        _use(client, devin)
        assert client.get("/api/auth/session").status_code == 401
        # Decided requests cannot be decided again; unknown users are 404.
        _use(client, admin)
        again = client.post(f"{ROUTE}/devin/approve", json={})
        assert again.status_code == 409
        assert again.json() == {"detail": "That request was already decided."}
        assert client.post(f"{ROUTE}/nobody/decline", json={}).status_code == 404
        assert client.post(f"{ROUTE}/zac/approve", json={}).status_code == 409
        # Nobody decides their own admission.
        assert client.post(f"{ROUTE}/{TEST_OWNER}/approve", json={}).status_code == (
            403
        )
        # The body must be the explicit empty object; path values are bounded.
        assert client.post(
            f"{ROUTE}/zac/approve", json={"force": True}
        ).status_code == (422)
        assert client.post(f"{ROUTE}/{'x' * 129}/approve", json={}).status_code == 422
        assert client.post(f"{ROUTE}/a%20b/approve", json={}).status_code == 422


def test_decisions_need_explicit_application_requests_and_are_rate_limited(
    tmp_path: Path,
) -> None:
    path = tmp_path / "db.sqlite3"
    with session_client(path) as client:
        for headers in ({"x-ww-request": "0"}, {"origin": ""}):
            assert (
                client.post(f"{ROUTE}/x/approve", json={}, headers=headers).status_code
                == 403
            )
        assert client.post(f"{ROUTE}/x/approve", content="x").status_code == 415
        codes = [
            client.post(f"{ROUTE}/nobody/approve", json={}).status_code
            for _ in range(DECISION_LIMIT + 1)
        ]
        assert codes == [404] * DECISION_LIMIT + [429]
        limited = client.post(f"{ROUTE}/nobody/decline", json={})
        assert limited.status_code == 429
        assert int(limited.headers["retry-after"]) >= 1
        assert limited.headers["cache-control"] == "no-store"
        # Reads are not limited, and another user has an independent allowance.
        assert client.get(ROUTE).status_code == 200
        _use(client, member_cookie(path, "other"))
        assert client.post(f"{ROUTE}/nobody/approve", json={}).status_code == 404
        client.cookies.clear()
        for method, url in (
            ("GET", ROUTE),
            ("POST", f"{ROUTE}/x/approve"),
            ("POST", f"{ROUTE}/x/decline"),
            ("GET", ADMIN_ROUTE),
            ("POST", f"{ADMIN_ROUTE}/x/remove"),
            ("POST", f"{ADMIN_ROUTE}/x/reinstate"),
        ):
            response = client.request(method, url, json={})
            assert response.status_code == 401, (method, url)
            assert response.headers["cache-control"] == "no-store"


def test_admin_sees_the_tree_removes_and_reinstates(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    with session_client(path) as client:
        admin = client.cookies.get(COOKIE)
        assert admin is not None
        zac = member_cookie(path, "zac")
        _snapshot(path, "zac", "Person Z", "zac@example.invalid", 100)
        member_cookie(path, "devin", approved=False)
        member_cookie(path, "henry", approved=False)
        approve_member(path, "devin", by="zac")
        approve_member(path, "henry", by="devin")
        token = client.post("/api/account/api-token/reset", json={}).json()["token"]
        # Members never see the tree or remove anyone.
        _use(client, zac)
        assert client.get(ADMIN_ROUTE).status_code == 403
        assert client.post(f"{ADMIN_ROUTE}/devin/remove", json={}).status_code == 403
        zac_token = client.post("/api/account/api-token/reset", json={}).json()["token"]
        _use(client, admin)
        tree = client.get(ADMIN_ROUTE)
        assert tree.status_code == 200
        assert tree.headers["cache-control"] == "no-store"
        root = tree.json()["root"]
        assert root["id"] == TEST_OWNER and root["role"] == "admin"
        assert [child["id"] for child in root["children"]] == ["zac"]
        assert root["children"][0]["approved_by"] is None
        assert root["children"][0]["email"] == "zac@example.invalid"
        assert [child["id"] for child in root["children"][0]["children"]] == ["devin"]
        assert root["children"][0]["children"][0]["approved_by"] == "Person Z"
        assert tree.json()["declined"] == []
        # Removing Zac re-parents Devin under the admin and ends Zac's access.
        removed = client.post(f"{ADMIN_ROUTE}/zac/remove", json={})
        assert removed.status_code == 200
        root = removed.json()["root"]
        assert [child["id"] for child in root["children"]] == ["devin"]
        assert [child["id"] for child in root["children"][0]["children"]] == ["henry"]
        assert [
            (person["id"], person["status"]) for person in removed.json()["declined"]
        ] == [("zac", "removed")]
        client.cookies.clear()
        bearer = {"authorization": f"Bearer {zac_token}"}
        assert client.get("/api/agent/v1/me", headers=bearer).status_code == 401
        bearer = {"authorization": f"Bearer {token}"}
        assert client.get("/api/agent/v1/me", headers=bearer).status_code == 200
        _use(client, zac)
        assert client.get("/api/auth/session").status_code == 401
        _use(client, admin)
        assert client.post(f"{ADMIN_ROUTE}/zac/remove", json={}).status_code == 409
        assert client.post(f"{ADMIN_ROUTE}/nobody/remove", json={}).status_code == 404
        forbidden = client.post(f"{ADMIN_ROUTE}/{TEST_OWNER}/remove", json={})
        assert forbidden.status_code == 403
        assert forbidden.json() == {"detail": "The administrator cannot be removed."}
        assert client.post(f"{ADMIN_ROUTE}/devin/reinstate", json={}).status_code == (
            409
        )
        back = client.post(f"{ADMIN_ROUTE}/zac/reinstate", json={})
        assert back.status_code == 200
        assert back.json()["declined"] == []
        assert [person["id"] for person in client.get(ROUTE).json()["pending"]] == [
            "zac"
        ]
        assert ApprovalStore(path).pending_count() == 1


def test_sse_stream_ends_when_approval_is_withdrawn(tmp_path: Path) -> None:
    import asyncio
    from typing import NoReturn

    from fastapi import Request

    from waterlooworks_flash_next.api import session_event_stream
    from waterlooworks_flash_next.app_auth import SessionStore
    from waterlooworks_flash_next.refresh import RefreshService

    path = tmp_path / "db.sqlite3"
    with session_client(path) as _client:
        cookie = member_cookie(path, "zac")

        async def receive() -> NoReturn:
            await asyncio.Future()
            raise AssertionError("Unreachable")

        request = Request(
            {"type": "http", "headers": [(b"cookie", f"{COOKIE}={cookie}".encode())]},
            receive=receive,
        )

        async def consume() -> None:
            iterator = session_event_stream(
                request, RefreshService(path, None), SessionStore(path)
            )
            assert "event: refresh" in await anext(iterator)
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "UPDATE user_approvals SET status='pending', parent_uid=NULL, "
                    "decided_by=NULL, decided_at=NULL WHERE uid='zac'"
                )
            try:
                await anext(iterator)
            except StopAsyncIteration:
                return
            raise AssertionError("Stream continued without approval")

        asyncio.run(consume())
        now = int(time.time())
        assert now > 0
