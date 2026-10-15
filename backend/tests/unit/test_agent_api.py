"""Bearer-token agent API on real empty/ID-only SQLite; no platform data.

Tokens come from the real account reset route; no cookie is ever sent to the
agent routes. Posting rows are structural ID-only records.
"""

import logging
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from auth_support import approve_member, session_client
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from waterlooworks_flash_next.agent_api import RATE_LIMIT, RATE_WINDOW_SECONDS
from waterlooworks_flash_next.api import create_app
from waterlooworks_flash_next.app_auth import COOKIE, SessionStore
from waterlooworks_flash_next.approvals import ApprovalStore
from waterlooworks_flash_next.database import (
    StoredListing,
    open_database,
    save_listings,
    start_collection_run,
)

BASE = "/api/agent/v1"
REPO_ROOT = Path(__file__).resolve().parents[3]
SUPPRESSED = {"detail": "Invalid request; input diagnostics suppressed."}


def _postings(path: Path, *ids: str) -> None:
    connection = open_database(path)
    run_id = start_collection_run(connection)
    save_listings(connection, run_id, [StoredListing(id=pid) for pid in ids])
    connection.close()


@contextmanager
def agent_client(
    path: Path, *, member: bool = False
) -> Iterator[tuple[TestClient, str]]:
    """Issue a token through the session route, then drop the cookie entirely."""
    with session_client(path) as client:
        if member:
            store = SessionStore(path)
            now = int(time.time())
            cookie = store.issue(
                store.prepare(None), "member-uid", now + 300, auth_time=now
            ).cookie
            approve_member(path, "member-uid")
            client.cookies.clear()
            client.cookies.set(COOKIE, cookie, domain="localhost.local", path="/api")
        token = client.post("/api/account/api-token/reset", json={}).json()["token"]
        client.cookies.clear()
        client.headers.clear()
        client.headers["authorization"] = f"Bearer {token}"
        yield client, token


def _flatten(routes: Iterable[object]) -> Iterator[tuple[str, str]]:
    """Every registered operation, including routers FastAPI nests on include."""
    for route in routes:
        if isinstance(route, APIRoute):
            for method in route.methods or set():
                yield route.path, method
        nested = getattr(route, "original_router", None)
        if nested is not None:
            yield from _flatten(nested.routes)


def test_router_exposes_only_read_and_mark_routes(tmp_path: Path) -> None:
    app = create_app(tmp_path / "db.sqlite3")
    registered = set(_flatten(app.routes))
    assert len(registered) > 20  # the flattening really saw the whole app
    routes = {
        (path, method) for path, method in registered if path.startswith("/api/agent/")
    }
    spec = app.openapi()
    documented = {
        (path, method.upper())
        for path, operations in spec["paths"].items()
        if path.startswith("/api/agent/")
        for method in operations
    }
    assert documented == routes
    assert routes == {
        (f"{BASE}/me", "GET"),
        (f"{BASE}/refresh", "GET"),
        (f"{BASE}/postings", "GET"),
        (f"{BASE}/postings/{{posting_id}}", "GET"),
        (f"{BASE}/postings/{{posting_id}}/state", "PUT"),
        (f"{BASE}/postings/{{posting_id}}/viewed", "PUT"),
        (f"{BASE}/openapi.json", "GET"),
        (f"{BASE}/guide", "GET"),
    }
    assert not any(method in {"POST", "DELETE", "PATCH"} for _, method in routes)
    assert spec["components"]["securitySchemes"]["AgentToken"]["scheme"] == "bearer"
    public = {f"{BASE}/openapi.json", f"{BASE}/guide"}
    for path, method in routes:
        operation = spec["paths"][path][method.lower()]
        assert operation["operationId"]
        assert operation["description"]
        if path in public:
            assert "security" not in operation
        else:
            assert operation["security"] == [{"AgentToken": []}]


def _refs(node: object) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                yield value
            else:
                yield from _refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from _refs(value)


def test_public_agent_schema_lists_only_agent_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WW_PROJECT_ROOT", str(REPO_ROOT))
    with session_client(tmp_path / "db.sqlite3") as client:
        client.cookies.clear()
        _public_routes(client)


def _public_routes(client: TestClient) -> None:
    response = client.get(f"{BASE}/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["openapi"].startswith("3.")
    assert schema["paths"]
    assert all(path.startswith(f"{BASE}/") for path in schema["paths"])
    assert f"{BASE}/postings" in schema["paths"]
    assert set(schema["components"]["securitySchemes"]) == {"AgentToken"}
    names = set(schema["components"]["schemas"])
    for ref in _refs(schema):
        assert ref.removeprefix("#/components/schemas/") in names
    # Only the closure of agent references: no admin or settings shapes.
    assert not any("Admin" in name or "AISettings" in name for name in names)
    assert "Agent API" in schema["info"]["description"]


def test_public_agent_guide_is_markdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WW_PROJECT_ROOT", str(REPO_ROOT))
    with session_client(tmp_path / "db.sqlite3") as client:
        client.cookies.clear()
        _public_markdown(client)


def _public_markdown(client: TestClient) -> None:
    response = client.get(f"{BASE}/guide")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.text.startswith("# Agent API")


def test_missing_bad_replaced_and_revoked_tokens_are_generic_401(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "db.sqlite3"
    logging.getLogger("api").addHandler(caplog.handler)
    try:
        with agent_client(path) as (client, token):
            assert client.get(f"{BASE}/me").status_code == 200
            for headers in (
                {},
                {"authorization": ""},
                {"authorization": "Bearer"},
                {"authorization": "Bearer "},
                {"authorization": f"Basic {token}"},
                {"authorization": f"Bearer {token}x"},
                {"authorization": f"Bearer {token[:-1]}"},
                {"authorization": f"Bearer {token.upper()}"},
                {"authorization": "Bearer wwf_" + "a" * 43},
                {"authorization": f"Bearer {token}", "cookie": "ww_session=abc"},
            ):
                client.headers.clear()
                response = client.get(f"{BASE}/me", headers=headers)
                if headers.get("cookie"):
                    assert response.status_code == 200  # cookies are ignored
                    continue
                assert response.status_code == 401, headers
                assert response.headers["www-authenticate"] == "Bearer"
                assert response.headers["cache-control"] == "no-store"
                assert response.json() == {"detail": "Agent token was not accepted."}
                assert token not in response.text
            # Query-string tokens are never accepted.
            assert client.get(f"{BASE}/me?token={token}").status_code == 401
            assert client.get(f"{BASE}/me?access_token={token}").status_code == 401
            # The app session cookie alone never authenticates an agent route.
            with session_client(path) as cookie_client:
                assert cookie_client.get(f"{BASE}/me").status_code == 401
                replaced = cookie_client.post(
                    "/api/account/api-token/reset", json={}
                ).json()["token"]
            client.headers["authorization"] = f"Bearer {token}"
            assert client.get(f"{BASE}/me").status_code == 401
            client.headers["authorization"] = f"Bearer {replaced}"
            assert client.get(f"{BASE}/me").status_code == 200
            with session_client(path) as cookie_client:
                cookie_client.request("DELETE", "/api/account/api-token", json={})
            assert client.get(f"{BASE}/me").status_code == 401
    finally:
        logging.getLogger("api").removeHandler(caplog.handler)
    assert token not in caplog.text
    assert "wwf_" not in caplog.text


def test_token_dies_when_the_user_loses_access(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    with agent_client(path, member=True) as (client, _token):
        assert client.get(f"{BASE}/me").json()["role"] == "member"
        # The administrator removes the member: the token stops at once.
        ApprovalStore(path).remove("internal-session-test", "member-uid")
        assert client.get(f"{BASE}/me").status_code == 401


def test_token_of_a_pending_user_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    with agent_client(path, member=True) as (client, _token):
        assert client.get(f"{BASE}/me").status_code == 200
        with open_database(path) as connection:
            # A migrated or reinstated member waits again: no agent access.
            connection.execute(
                "UPDATE user_approvals SET status='pending', parent_uid=NULL, "
                "decided_by=NULL, decided_at=NULL WHERE uid='member-uid'"
            )
        assert client.get(f"{BASE}/me").status_code == 401


def test_identity_refresh_and_empty_search(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    with agent_client(path) as (client, _token):
        me = client.get(f"{BASE}/me")
        assert me.status_code == 200
        assert me.headers["cache-control"] == "no-store"
        assert me.json() == {
            "uid": "internal-session-test",
            "role": "admin",
            "profile": None,
        }
        refresh = client.get(f"{BASE}/refresh")
        assert refresh.status_code == 200
        assert refresh.json() == {
            "running": False,
            "status": "idle",
            "last_refreshed_at": None,
            "last_run_finished_at": None,
            "next_scheduled_at": None,
        }
        page = client.get(f"{BASE}/postings")
        assert page.status_code == 200
        assert page.json() == {
            "page": 1,
            "page_size": 20,
            "total": 0,
            "total_pages": 0,
            "postings": [],
        }
        missing = client.get(f"{BASE}/postings/1")
        assert missing.status_code == 404
        assert missing.json()["detail"] == "Stored posting not found."
        for url in (f"{BASE}/postings/abc", f"{BASE}/postings?page=0"):
            assert client.get(url).json() == SUPPRESSED
        assert client.get(f"{BASE}/postings?unknown=1").status_code == 422
        marker = "zz-marker-zz"
        bad = client.get(f"{BASE}/postings?sort={marker}")
        assert bad.status_code == 422 and marker not in bad.text


def test_search_read_and_marks_with_structural_rows(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    _postings(path, "1", "2", "3")
    with agent_client(path) as (client, _token):
        page = client.get(f"{BASE}/postings?page_size=2").json()
        assert page["total"] == 3 and page["total_pages"] == 2
        assert [p["posting_id"] for p in page["postings"]] == ["1", "2"]
        summary = page["postings"][0]
        assert summary["state"] is None and summary["viewed_at"] is None
        assert set(summary) == {
            "posting_id",
            "title",
            "organization",
            "division",
            "location",
            "openings",
            "level",
            "application_count",
            "deadline",
            "work_arrangement",
            "country_code",
            "country_name",
            "targeted_disciplines",
            "compensation",
            "first_seen_at",
            "last_seen_at",
            "state",
            "viewed_at",
        }
        second = client.get(f"{BASE}/postings?page_size=2&page=2").json()
        assert [p["posting_id"] for p in second["postings"]] == ["3"]
        marked = client.put(f"{BASE}/postings/2/state", json={"state": "applied"})
        assert marked.status_code == 200
        assert marked.json()["state"] == "applied"
        assert marked.headers["cache-control"] == "no-store"
        viewed = client.put(f"{BASE}/postings/3/viewed", json={"viewed": True})
        assert viewed.json()["viewed_at"] is not None
        listed = {
            p["posting_id"]: p
            for p in client.get(f"{BASE}/postings").json()["postings"]
        }
        assert listed["2"]["state"] == "applied"
        assert listed["3"]["viewed_at"] == viewed.json()["viewed_at"]
        assert [
            p["posting_id"]
            for p in client.get(f"{BASE}/postings?state=none").json()["postings"]
        ] == ["1", "3"]
        assert [
            p["posting_id"]
            for p in client.get(f"{BASE}/postings?viewed=true").json()["postings"]
        ] == ["3"]
        assert client.get(f"{BASE}/postings?q=2").json()["total"] == 1
        detail = client.get(f"{BASE}/postings/2")
        assert detail.status_code == 200
        body = detail.json()
        assert body["posting_id"] == "2" and body["state"] == "applied"
        assert body["viewed_at"] is None  # reading never marks viewed
        assert body["content"] is None and body["content_observed_at"] is None
        assert body["analysis"]["status"] == "unavailable"
        assert "manual_refresh_available" not in body
        assert client.get(f"{BASE}/postings/2").json()["viewed_at"] is None
        cleared = client.put(f"{BASE}/postings/2/state", json={"state": None})
        assert cleared.json()["state"] is None
        for url, payload in (
            (f"{BASE}/postings/2/state", {"state": "maybe"}),
            (f"{BASE}/postings/2/state", {}),
            (f"{BASE}/postings/2/viewed", {"viewed": "yes"}),
        ):
            response = client.put(url, json=payload)
            assert response.status_code == 422
            assert response.json() == SUPPRESSED
        assert (
            client.put(f"{BASE}/postings/9/state", json={"state": None}).status_code
            == 404
        )
        assert (
            client.put(f"{BASE}/postings/9/viewed", json={"viewed": True}).status_code
            == 404
        )
        # Bearer mutations need JSON but no Origin/X-WW-Request CSRF headers.
        assert client.put(f"{BASE}/postings/2/viewed", content="x").status_code == 415
        # Session-only marks are the same marks the agent sees.
        with session_client(path) as cookie_client:
            marks = cookie_client.get("/api/account/posting-states").json()["marks"]
        assert set(marks) == {"3"}


def test_member_token_sees_member_data_and_private_marks(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    _postings(path, "1")
    with agent_client(path, member=True) as (client, _token):
        assert client.get(f"{BASE}/me").json()["role"] == "member"
        client.put(f"{BASE}/postings/1/state", json={"state": "interested"})
    with agent_client(path) as (admin, _token):
        assert admin.get(f"{BASE}/postings/1").json()["state"] is None
        spec = admin.app.openapi()  # type: ignore[attr-defined]
        agent_schemas = {
            name for name in spec["components"]["schemas"] if name.startswith("Agent")
        }
        for name in agent_schemas:
            properties = spec["components"]["schemas"][name].get("properties", {})
            assert "manual_refresh_available" not in properties
            assert "logs" not in properties


def test_requests_are_rate_limited_per_token(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    with agent_client(path) as (client, token):
        codes = [client.get(f"{BASE}/me").status_code for _ in range(RATE_LIMIT)]
        assert codes == [200] * RATE_LIMIT
        limited = client.get(f"{BASE}/me")
        assert limited.status_code == 429
        assert 1 <= int(limited.headers["retry-after"]) <= RATE_WINDOW_SECONDS
        assert limited.headers["cache-control"] == "no-store"
        assert token not in limited.text
        with session_client(path) as cookie_client:
            other = cookie_client.post("/api/account/api-token/reset", json={}).json()
        client.headers["authorization"] = f"Bearer {other['token']}"
        assert client.get(f"{BASE}/me").status_code == 200
