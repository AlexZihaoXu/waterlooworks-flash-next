"""Real empty API authorization; readiness is the only test-only override."""

import sqlite3
import time
from pathlib import Path

import pytest
from auth_support import AUTH_HEADERS, configuration_ready, session_client
from fastapi.testclient import TestClient

from waterlooworks_flash_next.api import create_app
from waterlooworks_flash_next.app_auth import (
    COOKIE,
    AuthError,
    AuthTransport,
    BootstrapOwner,
    SessionStore,
    require_firebase_ready,
)

PROTECTED = (
    "/api/workspace",
    "/api/admin/openai-usage",
    "/api/admin/refresh-auth",
    "/api/postings/1",
    "/api/postings/1/analysis",
    "/api/postings/1/history",
    "/api/postings/1/comparisons",
    "/api/keyword-index",
    "/api/exchange-rates",
    "/api/refresh",
    "/api/refresh/events?once=true",
    "/api/auth/session",
    "/api/auth/profile",
    "/api/auth/profile?uid=other-uid",
)


def test_all_reads_require_real_session_and_are_no_store(tmp_path: Path) -> None:
    app = create_app(tmp_path / "db.sqlite3")
    app.dependency_overrides[require_firebase_ready] = configuration_ready
    with TestClient(app, base_url="https://localhost") as client:
        for path in PROTECTED:
            response = client.get(path)
            assert response.status_code == 401, path
            assert response.headers["cache-control"] == "no-store"
        assert client.get("/api/health").status_code == 200
        assert (
            client.post("/api/refresh", json={}, headers=AUTH_HEADERS).status_code
            == 401
        )


def test_profile_rejects_caller_selected_uid_without_provider_io(
    tmp_path: Path,
) -> None:
    path = tmp_path / "db.sqlite3"
    with session_client(path) as client:
        response = client.get("/api/auth/profile?uid=other-uid")
        assert response.status_code == 400
        assert response.headers["cache-control"] == "no-store"
        # The real session is still usable; this optional read never claims a user.
        assert client.get("/api/auth/session").status_code == 200
        with sqlite3.connect(path) as connection:
            assert (
                connection.execute("SELECT count(*) FROM app_users").fetchone()[0] == 1
            )
            assert (
                connection.execute("SELECT count(*) FROM collection_runs").fetchone()[0]
                == 0
            )


def test_profile_failure_does_not_change_session_or_start_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT", "!")
    path = tmp_path / "db.sqlite3"
    with session_client(path) as client:
        before = client.get("/api/auth/session").json()
        response = client.get("/api/auth/profile")
        assert response.status_code == 503
        assert response.json() == {"detail": "Account profile unavailable."}
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/api/auth/session").json() == before
        assert client.get("/api/workspace").status_code == 200
        with sqlite3.connect(path) as connection:
            assert (
                connection.execute("SELECT count(*) FROM app_users").fetchone()[0] == 1
            )
            assert (
                connection.execute("SELECT count(*) FROM collection_runs").fetchone()[0]
                == 0
            )


def test_profile_denies_prepared_expired_and_logged_out_sessions(
    tmp_path: Path,
) -> None:
    path = tmp_path / "db.sqlite3"
    app = create_app(path)
    app.dependency_overrides[require_firebase_ready] = configuration_ready
    with TestClient(app, base_url="https://localhost") as client:
        store = SessionStore(path, bootstrap=BootstrapOwner(uid="internal-uid"))
        cookie = store.prepare(None)
        client.cookies.set(COOKIE, cookie, domain="localhost.local", path="/api")
        assert client.get("/api/auth/profile").status_code == 401
        now = int(time.time())
        cookie = store.issue(cookie, "internal-uid", now + 300, auth_time=now).cookie
        store.logout(cookie)
        assert client.get("/api/auth/profile").status_code == 401
        cookie = store.prepare(None, now=100)
        cookie = store.issue(cookie, "internal-uid", 200, auth_time=100, now=100).cookie
        client.cookies.clear()
        client.cookies.set(COOKIE, cookie, domain="localhost.local", path="/api")
        assert client.get("/api/auth/profile").status_code == 401


def test_member_cannot_mutate_and_logout_invalidates_cookie(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    with session_client(path) as client:
        store = SessionStore(path)
        cookie = store.prepare(None)
        now = int(time.time())
        member = store.issue(cookie, "ordinary-uid", now + 300, auth_time=now)
        cookie = member.cookie
        assert member.identity.role == "member"
        client.cookies.clear()
        client.cookies.set(COOKIE, cookie, domain="localhost.local", path="/api")
        assert client.get("/api/workspace").json()["manual_refresh_available"] is False
        assert client.get("/api/auth/session").json()["role"] == "member"
        challenge = client.get("/api/admin/refresh-auth")
        assert challenge.status_code == 403
        assert challenge.headers["cache-control"] == "no-store"
        usage = client.get("/api/admin/openai-usage")
        assert usage.status_code == 403
        assert usage.headers["cache-control"] == "no-store"
        assert client.post("/api/refresh", json={}).status_code == 403
        response = client.post("/api/auth/logout", json={})
        assert response.status_code == 200
        assert "HttpOnly" in response.headers["set-cookie"]
        assert "Secure" in response.headers["set-cookie"]
        assert "SameSite=strict" in response.headers["set-cookie"]
        assert store.lookup(cookie) is None
        client.cookies.set(COOKIE, cookie, domain="localhost.local", path="/api")
        assert client.get("/api/workspace").status_code == 401
        with pytest.raises(AuthError):
            store.issue(cookie, "ordinary-uid", now + 300, auth_time=now)


def test_csrf_cookie_and_body_bounds(tmp_path: Path) -> None:
    with session_client(tmp_path / "db.sqlite3") as client:
        response = client.post("/api/auth/prepare", json={})
        assert response.status_code == 200
        cookie = response.headers["set-cookie"]
        for flag in (
            "HttpOnly",
            "Secure",
            "SameSite=strict",
            "Path=/api",
            "Max-Age=3600",
        ):
            assert flag in cookie
        for endpoint in ("prepare", "exchange", "logout"):
            for headers in (
                {"origin": ""},
                {"origin": "https://evil.invalid"},
                {"x-ww-request": ""},
            ):
                assert (
                    client.post(
                        f"/api/auth/{endpoint}", json={}, headers=headers
                    ).status_code
                    == 403
                )
        assert (
            client.post(
                "/api/auth/logout",
                content="x" * 20001,
                headers={"content-type": "application/json"},
            ).status_code
            == 413
        )
        assert client.post(
            "/api/auth/logout", json={"unexpected": "private"}
        ).json() == {"detail": "Invalid request; input diagnostics suppressed."}


def test_http_is_denied_by_default_and_forwarded_headers_not_trusted(
    tmp_path: Path,
) -> None:
    app = create_app(tmp_path / "db.sqlite3")
    with TestClient(app, base_url="http://localhost") as client:
        assert (
            client.get(
                "/api/workspace", headers={"x-forwarded-proto": "https"}
            ).status_code
            == 403
        )
        assert (
            client.post("/api/auth/prepare", json={}, headers=AUTH_HEADERS).status_code
            == 403
        )


def test_loopback_exception_cannot_allow_insecure_lan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WW_AUTH_ALLOW_LOOPBACK_HTTP", "1")
    monkeypatch.setenv("WW_AUTH_ORIGINS", "http://main-pc:5173")
    with pytest.raises(AuthError):
        AuthTransport.from_environment()
    monkeypatch.setenv("WW_AUTH_ORIGINS", "http://127.0.0.1:5173")
    app = create_app(tmp_path / "db.sqlite3")
    app.dependency_overrides[require_firebase_ready] = configuration_ready
    with TestClient(
        app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)
    ) as client:
        response = client.post(
            "/api/auth/prepare",
            json={},
            headers={"origin": "http://127.0.0.1:5173", "x-ww-request": "1"},
        )
        assert (
            response.status_code == 200
        )  # Vite changed Host, retained allowed Origin.
        assert "Secure" not in response.headers["set-cookie"]
    with TestClient(
        app, base_url="http://127.0.0.1", client=("192.168.1.10", 12345)
    ) as client:
        assert client.get("/api/workspace").status_code == 403


def test_auth_schema_upgrade_is_additive_and_bootstrap_cannot_reset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from waterlooworks_flash_next.database import open_database

    path = tmp_path / "db.sqlite3"
    from waterlooworks_flash_next import database

    with monkeypatch.context() as legacy:
        legacy.setattr(database, "SCHEMA_VERSION", 13)
        connection = open_database(path)
    # Empty v10 boundary, not fabricated source records.
    connection.execute("DROP TABLE openai_reauth_challenges")
    connection.execute("DROP TABLE openai_credentials")
    connection.execute("DROP TABLE waterlooworks_credentials")
    connection.execute("DROP TABLE app_sessions")
    connection.execute("DROP TABLE app_users")
    connection.execute("PRAGMA user_version=10")
    before = set(
        connection.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE name<>'inference_jobs'"
        )
    )
    connection.close()
    connection = open_database(path)
    after = set(connection.execute("SELECT type, name, sql FROM sqlite_master"))
    assert before <= after
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()
    store = SessionStore(path, bootstrap=BootstrapOwner(uid="owner-uid"))
    cookie = store.prepare(None)
    now = int(time.time())
    store.issue(cookie, "owner-uid", now + 300, auth_time=now)
    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM app_users")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE app_users SET role='member'")


def test_workspace_cache_never_shares_admin_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "db.sqlite3"
    monkeypatch.setenv("WW_DATABASE", str(path))
    monkeypatch.setenv("WW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("WW_ENABLE_REFRESH", "1")
    with session_client(path, app=create_app()) as client:
        store = SessionStore(path)
        admin_cookie = client.cookies.get(COOKIE)
        member_cookie = store.prepare(None)
        now = int(time.time())
        member_cookie = store.issue(
            member_cookie, "member-uid", now + 300, auth_time=now
        ).cookie
        # All provisioning precedes these reads; the global snapshot stays cached.
        for cookie, expected in (
            (admin_cookie, True),
            (member_cookie, False),
            (admin_cookie, True),
        ):
            client.cookies.clear()
            assert cookie is not None
            client.cookies.set(COOKIE, cookie, domain="localhost.local", path="/api")
            data = client.get("/api/workspace").json()
            assert data["manual_refresh_available"] is expected
            assert "uid" not in data and "role" not in data
        with sqlite3.connect(path) as connection:
            assert (
                connection.execute("SELECT count(*) FROM collection_runs").fetchone()[0]
                == 0
            )


@pytest.mark.parametrize("invalidate", ["logout", "expiry"])
def test_open_sse_stops_after_logout_or_expiry(tmp_path: Path, invalidate: str) -> None:
    import asyncio
    from typing import NoReturn

    from fastapi import Request

    from waterlooworks_flash_next.api import session_event_stream
    from waterlooworks_flash_next.refresh import RefreshService

    path = tmp_path / "db.sqlite3"
    app = create_app(path)
    with session_client(path, app=app) as client:
        cookie = client.cookies.get(COOKIE)

        async def receive() -> NoReturn:
            await asyncio.Future()
            raise AssertionError("Unreachable")

        request = Request(
            {"type": "http", "headers": [(b"cookie", f"{COOKIE}={cookie}".encode())]},
            receive=receive,
        )

        async def consume() -> None:
            # Exercise the real streaming body with a primitive ASGI request.
            iterator = session_event_stream(
                request, RefreshService(path, None), SessionStore(path)
            )
            assert "event: refresh" in await anext(iterator)
            if invalidate == "logout":
                SessionStore(path).logout(cookie)
            else:
                with sqlite3.connect(path) as connection:
                    connection.execute("UPDATE app_sessions SET expires_at=0")
            with pytest.raises(StopAsyncIteration):
                await anext(iterator)

        asyncio.run(consume())


def test_admin_refresh_challenge_empty_read_does_not_start_work(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    with session_client(path) as client:
        response = client.get("/api/admin/refresh-auth")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {
            "run_id": None,
            "approval_required": False,
            "code": None,
            "expires_in_seconds": 0,
        }
        with sqlite3.connect(path) as connection:
            assert (
                connection.execute("SELECT count(*) FROM collection_runs").fetchone()[0]
                == 0
            )


def test_current_challenge_is_admin_only_and_never_in_refresh_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import secrets

    from waterlooworks_flash_next import api
    from waterlooworks_flash_next.api_models import RefreshSnapshot
    from waterlooworks_flash_next.login import AuthDeadline
    from waterlooworks_flash_next.refresh import RefreshService

    services: list[RefreshService] = []

    def capture_service(
        path: Path,
        root: Path | None,
        *,
        detail_limit: int | None = None,
        full_refetch: bool = False,
    ) -> RefreshService:
        service = RefreshService(
            path, root, detail_limit=detail_limit, full_refetch=full_refetch
        )
        services.append(service)
        return service

    # Observe the real service; no authentication or provider behavior is replaced.
    monkeypatch.setattr(api, "RefreshService", capture_service)
    path = tmp_path / "empty.sqlite3"
    with session_client(path) as client:
        service = services[0]
        service.state = RefreshSnapshot(run_id="current", status="running")
        service.auth_challenge.begin("current", AuthDeadline())
        code = f"{secrets.randbelow(1000):03d}"
        service.auth_challenge.publish("current", code)
        response = client.get("/api/admin/refresh-auth")
        assert response.status_code == 200
        assert (response.json()["code"] == code) is True
        assert response.headers["cache-control"] == "no-store"
        assert "code" not in client.get("/api/refresh").json()
        assert '"code":' not in client.get("/api/refresh/events?once=true").text
        service.state.run_id = "different"
        assert client.get("/api/admin/refresh-auth").json()["code"] is None
        service.state.run_id = "current"
        service.state.status = "failed"
        assert client.get("/api/admin/refresh-auth").json()["code"] is None
        service.auth_challenge.clear("current")
        client.post("/api/auth/logout", json={})
        assert client.get("/api/admin/refresh-auth").status_code == 401


def test_auth_storage_overload_is_503_with_retry_after(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    with session_client(path) as client:
        blocker = sqlite3.connect(path, isolation_level=None)
        try:
            blocker.execute("BEGIN IMMEDIATE")
            response = client.post("/api/auth/prepare", json={})
        finally:
            blocker.execute("ROLLBACK")
            blocker.close()
        assert response.status_code == 503
        assert response.headers["retry-after"] == "2"
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {
            "detail": "Authentication is busy. Try again shortly."
        }
        assert client.post("/api/auth/prepare", json={}).status_code == 200


def test_admin_responses_are_always_no_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import secrets

    monkeypatch.delenv("WW_CREDENTIALS_KEY", raising=False)
    monkeypatch.delenv("WW_CREDENTIALS_KEY_FILE", raising=False)
    with session_client(tmp_path / "db.sqlite3") as client:
        responses = [
            client.get("/api/admin/openai-key"),
            client.get("/api/admin/waterlooworks-credentials"),
            client.get("/api/admin/refresh-auth"),
            client.post(
                "/api/admin/openai-key/reveal",
                json={
                    "challenge": secrets.token_urlsafe(32),
                    "id_token": secrets.token_urlsafe(32),
                },
            ),
        ]
        for response in responses:
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["pragma"] == "no-cache"


def test_unhandled_failure_logs_fixed_incident_line_without_secrets(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging
    import re
    import secrets

    secret = secrets.token_urlsafe(16)
    app = create_app(tmp_path / "db.sqlite3")

    @app.get("/api/test-only-failure/{value}")
    def failure(value: str) -> None:
        raise RuntimeError(secret + value)

    logger = logging.getLogger("api")
    logger.addHandler(caplog.handler)
    try:
        with TestClient(app, base_url="https://localhost") as client:
            response = client.get(f"/api/test-only-failure/{secret}")
    finally:
        logger.removeHandler(caplog.handler)
    assert response.status_code == 500
    assert response.headers["cache-control"] == "no-store"
    incident = response.json()["incident"]
    assert re.fullmatch(r"[0-9a-f]{8}", incident)
    assert response.json()["detail"] == (
        f"Request unavailable; diagnostics suppressed. Incident {incident}."
    )
    lines = [record.getMessage() for record in caplog.records]
    assert lines == [
        f"Unhandled request failure; incident {incident}; "
        "GET /api/test-only-failure/{value}; RuntimeError"
    ]
    assert secret not in caplog.text and secret not in response.text
