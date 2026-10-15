"""Per-user account routes: show-once API token and posting marks.

Real hashed sessions, roles and the transport/CSRF middleware run; only
Firebase readiness is overridden. Posting rows are structural ID-only
records; no platform content is invented.
"""

import time
from pathlib import Path

from auth_support import approve_member, session_client
from fastapi.testclient import TestClient

from waterlooworks_flash_next.api_tokens import TokenStore
from waterlooworks_flash_next.app_auth import COOKIE, SessionStore
from waterlooworks_flash_next.database import (
    StoredListing,
    open_database,
    save_listings,
    start_collection_run,
)

SUPPRESSED = {"detail": "Invalid request; input diagnostics suppressed."}
TOKEN = "/api/account/api-token"
STATES = "/api/account/posting-states"


def _postings(path: Path, *ids: str) -> None:
    connection = open_database(path)
    run_id = start_collection_run(connection)
    save_listings(connection, run_id, [StoredListing(id=pid) for pid in ids])
    connection.close()


def _member(client: TestClient, path: Path, uid: str = "member-uid") -> None:
    store = SessionStore(path)
    now = int(time.time())
    cookie = store.issue(store.prepare(None), uid, now + 300, auth_time=now).cookie
    approve_member(path, uid)
    client.cookies.clear()
    client.cookies.set(COOKIE, cookie, domain="localhost.local", path="/api")


def test_token_is_shown_once_then_only_metadata(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    with session_client(path) as client:
        empty = client.get(TOKEN)
        assert empty.status_code == 200
        assert empty.json() == {
            "configured": False,
            "hint": None,
            "created_at": None,
            "last_used_at": None,
        }
        assert empty.headers["cache-control"] == "no-store"
        issued = client.post(f"{TOKEN}/reset", json={})
        assert issued.status_code == 200
        assert issued.headers["cache-control"] == "no-store"
        assert issued.headers["pragma"] == "no-cache"
        body = issued.json()
        token = body["token"]
        assert token.startswith("wwf_") and len(token) == 47
        assert set(body) == {"token", "hint", "created_at"}
        assert body["hint"] == f"wwf_…{token[-4:]}"
        status = client.get(TOKEN).json()
        assert status["configured"] is True
        assert status["hint"] == body["hint"]
        assert status["created_at"] == body["created_at"]
        assert token not in client.get(TOKEN).text
        store = TokenStore(path)
        assert store.authenticate(token) is not None
        replaced = client.post(f"{TOKEN}/reset", json={}).json()["token"]
        assert replaced != token
        assert store.authenticate(token) is None
        assert store.authenticate(replaced) is not None
        revoked = client.request("DELETE", TOKEN, json={})
        assert revoked.status_code == 200
        assert revoked.json()["configured"] is False
        assert store.authenticate(replaced) is None
        assert client.request("DELETE", TOKEN, json={}).json()["configured"] is False


def test_reset_is_rate_limited_per_user(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    with session_client(path) as client:
        codes = [client.post(f"{TOKEN}/reset", json={}).status_code for _ in range(6)]
        assert codes == [200] * 5 + [429]
        limited = client.post(f"{TOKEN}/reset", json={})
        assert limited.status_code == 429
        assert int(limited.headers["retry-after"]) >= 1
        assert limited.headers["cache-control"] == "no-store"
        # Another user has an independent allowance.
        _member(client, path)
        assert client.post(f"{TOKEN}/reset", json={}).status_code == 200


def test_account_routes_require_session_and_explicit_application_requests(
    tmp_path: Path,
) -> None:
    path = tmp_path / "db.sqlite3"
    with session_client(path) as client:
        for method, url, body in (
            ("POST", f"{TOKEN}/reset", {}),
            ("DELETE", TOKEN, {}),
            ("PUT", f"{STATES}/1", {"state": "applied"}),
            ("PUT", f"{STATES}/1/viewed", {"viewed": True}),
        ):
            no_header = client.request(
                method, url, json=body, headers={"x-ww-request": "0"}
            )
            assert no_header.status_code == 403
            no_origin = client.request(method, url, json=body, headers={"origin": ""})
            assert no_origin.status_code == 403
            assert (
                client.request(method, url, content="x", headers={}).status_code == 415
            )
        client.cookies.clear()
        for method, url in (
            ("GET", TOKEN),
            ("POST", f"{TOKEN}/reset"),
            ("DELETE", TOKEN),
            ("GET", STATES),
            ("PUT", f"{STATES}/1"),
            ("PUT", f"{STATES}/1/viewed"),
        ):
            response = client.request(method, url, json={})
            assert response.status_code == 401, (method, url)
            assert response.headers["cache-control"] == "no-store"


def test_posting_marks_round_trip_for_members(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    with session_client(path) as client:
        _postings(path, "1", "2")
        _member(client, path)
        assert client.get(STATES).json() == {"marks": {}}
        missing = client.put(f"{STATES}/3", json={"state": "applied"})
        assert missing.status_code == 404
        assert missing.json()["detail"] == "Stored posting not found."
        assert client.put(f"{STATES}/3/viewed", json={"viewed": True}).status_code == (
            404
        )
        marked = client.put(f"{STATES}/1", json={"state": "interested"})
        assert marked.status_code == 200
        assert marked.headers["cache-control"] == "no-store"
        assert marked.json()["posting_id"] == "1"
        assert marked.json()["state"] == "interested"
        assert marked.json()["viewed_at"] is None
        viewed = client.put(f"{STATES}/2/viewed", json={"viewed": True}).json()
        assert viewed["state"] is None and viewed["viewed_at"] is not None
        marks = client.get(STATES).json()["marks"]
        assert set(marks) == {"1", "2"}
        assert marks["1"]["state"] == "interested"
        assert marks["2"]["viewed_at"] == viewed["viewed_at"]
        cleared = client.put(f"{STATES}/1", json={"state": None}).json()
        assert cleared["state"] is None and cleared["updated_at"] is None
        unviewed = client.put(f"{STATES}/2/viewed", json={"viewed": False}).json()
        assert unviewed["viewed_at"] is None
        assert client.get(STATES).json() == {"marks": {}}
        # Another user's marks stay private.
        client.put(f"{STATES}/1", json={"state": "applied"})
        _member(client, path, uid="other-uid")
        assert client.get(STATES).json() == {"marks": {}}


def test_mark_validation_errors_never_echo_input(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    with session_client(path) as client:
        _postings(path, "1")
        marker = "zz-secret-marker-zz"
        for url, body in (
            (f"{STATES}/1", {"state": marker}),
            (f"{STATES}/1", {"state": "applied", "extra": marker}),
            (f"{STATES}/1", {}),
            (f"{STATES}/1", {"state": 1}),
            (f"{STATES}/1/viewed", {"viewed": marker}),
            (f"{STATES}/1/viewed", {"viewed": "true"}),
            (f"{STATES}/1/viewed", {}),
            (f"{TOKEN}/reset", {"token": marker}),
        ):
            response = (
                client.put(url, json=body)
                if "viewed" in url or url.endswith("/1")
                else client.post(url, json=body)
            )
            assert response.status_code == 422, (url, body)
            assert response.json() == SUPPRESSED
            assert marker not in response.text
        for bad_id in ("abc", "-1", marker):
            response = client.put(f"{STATES}/{bad_id}", json={"state": "applied"})
            assert response.status_code == 422
            assert response.json() == SUPPRESSED
        assert client.get(STATES).json() == {"marks": {}}
