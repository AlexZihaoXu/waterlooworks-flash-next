"""Admin schedule routes and lifespan gating on empty SQLite.

Real hashed sessions, roles and the transport/CSRF middleware run; only
Firebase configuration readiness is overridden. No refresh is started.
"""

import time
from pathlib import Path

import pytest
from auth_support import session_client

from waterlooworks_flash_next.api import create_app
from waterlooworks_flash_next.app_auth import COOKIE, SessionStore

SUPPRESSED = {"detail": "Invalid request; input diagnostics suppressed."}
ROUTE = "/api/admin/refresh-schedule"


def test_admin_adds_lists_sorted_removes_and_toggles(tmp_path: Path) -> None:
    path = tmp_path / "empty.sqlite3"
    with session_client(path) as client:
        view = client.get(ROUTE)
        assert view.status_code == 200
        assert view.headers["cache-control"] == "no-store"
        assert view.json() == {
            "timezone": "America/Toronto",
            "times": [],
            "enabled": False,
            "server_enabled": False,
            "next_run_at": None,
            "min_interval_minutes": 30,
            "max_times": 24,
            "last": [],
            "failure": None,
        }
        for value in ("19:00", "07:30"):
            added = client.post(ROUTE, json={"time": value})
            assert added.status_code == 200
            assert added.headers["cache-control"] == "no-store"
        assert client.get(ROUTE).json()["times"] == ["07:30", "19:00"]
        toggled = client.put(f"{ROUTE}/enabled", json={"enabled": True})
        assert toggled.status_code == 200 and toggled.json()["enabled"] is True
        # The server gate is off for explicit database paths: no next run.
        assert toggled.json()["next_run_at"] is None
        removed = client.request("DELETE", ROUTE, json={"time": "07:30"})
        assert removed.status_code == 200 and removed.json()["times"] == ["19:00"]
        missing = client.request("DELETE", ROUTE, json={"time": "07:30"})
        assert missing.status_code == 404
        assert missing.json() == {"detail": "That time is not scheduled."}
        snapshot = client.get("/api/refresh").json()
        assert snapshot["next_scheduled_at"] is None
        assert snapshot["scheduled_failure"] is None


def test_validation_errors_are_fixed_and_never_echo_input(tmp_path: Path) -> None:
    path = tmp_path / "empty.sqlite3"
    with session_client(path) as client:
        assert client.post(ROUTE, json={"time": "07:30"}).status_code == 200
        close = client.post(ROUTE, json={"time": "07:45"})
        assert close.status_code == 422
        assert close.json() == {
            "detail": "Scheduled times must be at least 30 minutes apart."
        }
        duplicate = client.post(ROUTE, json={"time": "07:30"})
        assert duplicate.json() == {"detail": "That time is already scheduled."}
        for marker in ("25:61", "7:3xy"):
            bad = client.post(ROUTE, json={"time": marker})
            assert bad.status_code == 422
            assert bad.json() == {
                "detail": "Time must be HH:MM in 24-hour Toronto time."
            }
            assert marker not in bad.text
        for invalid in (
            {"time": "08:30", "extra": "marker-value"},
            {"time": 830},
            {},
        ):
            response = client.post(ROUTE, json=invalid)
            assert response.status_code == 422 and response.json() == SUPPRESSED
        malformed = client.post(
            ROUTE,
            content='{"time": "marker-value',
            headers={"content-type": "application/json"},
        )
        assert malformed.json() == SUPPRESSED
        assert client.put(f"{ROUTE}/enabled", json={"enabled": "yes"}).json() == (
            SUPPRESSED
        )


def test_routes_require_admin_and_explicit_application_requests(
    tmp_path: Path,
) -> None:
    path = tmp_path / "empty.sqlite3"
    with session_client(path) as client:
        for method, url, body in (
            ("POST", ROUTE, {"time": "07:30"}),
            ("DELETE", ROUTE, {"time": "07:30"}),
            ("PUT", f"{ROUTE}/enabled", {"enabled": True}),
        ):
            no_header = client.request(
                method, url, json=body, headers={"x-ww-request": "0"}
            )
            assert no_header.status_code == 403
            no_origin = client.request(method, url, json=body, headers={"origin": ""})
            assert no_origin.status_code == 403
        store = SessionStore(path)
        now = int(time.time())
        member = store.issue(
            store.prepare(None), "member-uid", now + 300, auth_time=now
        ).cookie
        client.cookies.clear()
        client.cookies.set(COOKIE, member, domain="localhost.local", path="/api")
        assert client.get(ROUTE).status_code == 403
        assert client.post(ROUTE, json={"time": "07:30"}).status_code == 403
        assert client.request("DELETE", ROUTE, json={"time": "07:30"}).status_code == (
            403
        )
        assert client.put(f"{ROUTE}/enabled", json={"enabled": True}).status_code == (
            403
        )
        # Members still read the shared, category-free schedule summary.
        member_snapshot = client.get("/api/refresh").json()
        assert set(member_snapshot) >= {"next_scheduled_at", "scheduled_failure"}
        client.cookies.clear()
        assert client.get(ROUTE).status_code == 401


@pytest.mark.parametrize(
    ("explicit", "refresh", "schedule", "expected"),
    [
        (False, "1", "1", True),
        (False, "1", None, False),
        (False, None, "1", False),
        (False, "1", "true", False),
        (True, "1", "1", False),
    ],
)
def test_scheduler_task_runs_only_behind_both_server_gates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    explicit: bool,
    refresh: str | None,
    schedule: str | None,
    expected: bool,
) -> None:
    path = tmp_path / "db.sqlite3"
    monkeypatch.setenv("WW_DATABASE", str(path))
    monkeypatch.setenv("WW_PROJECT_ROOT", str(tmp_path))
    for name, value in (
        ("WW_ENABLE_REFRESH", refresh),
        ("WW_ENABLE_SCHEDULE", schedule),
    ):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    app = create_app(path) if explicit else create_app()
    with session_client(path, app=app) as client:
        task = app.state.refresh_scheduler_task
        assert (task is not None and not task.done()) is expected
        assert client.get(ROUTE).json()["server_enabled"] is expected
    if expected:
        assert task.cancelled() or task.done()
