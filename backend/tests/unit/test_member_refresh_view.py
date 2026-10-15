"""Members see refresh timing and progress, never the log or diagnostics."""

import time
from pathlib import Path

from auth_support import session_client

from waterlooworks_flash_next.api_models import RefreshSnapshot, ScheduledFailureNotice
from waterlooworks_flash_next.app_auth import COOKIE, SessionStore
from waterlooworks_flash_next.refresh_view import MEMBER_FIELDS, member_refresh_view

PROGRESS = {
    "run_id",
    "status",
    "stage",
    "pages_completed",
    "total_pages",
    "details_completed",
    "details_total",
    "analysis_total",
    "analysis_completed",
    "eta_seconds",
    "next_scheduled_at",
    "scheduled_failure",
}


def test_member_view_keeps_only_timing_and_progress() -> None:
    assert set(MEMBER_FIELDS) == PROGRESS
    full = RefreshSnapshot(
        run_id="run-a",
        status="failed",
        stage="details",
        pages_completed=3,
        total_pages=9,
        records_received=150,
        details_completed=2,
        details_total=5,
        detail_requests=4,
        ww_requests=30,
        ww_request_budget=400,
        pacing_stop="ban_risk",
        error_category="response_schema",
        analysis_total=7,
        analysis_completed=1,
        analysis_rejected=1,
        analysis_provider="Provider / model",
        eta_seconds=60,
        logs=[
            {
                "sequence": 1,
                "at": "2026-10-15T00:00:00Z",
                "level": "info",
                "message": "x",
            }
        ],
        next_scheduled_at="2026-10-15T01:00:00Z",
        scheduled_failure=ScheduledFailureNotice(failed_at="2026-10-15T00:00:00Z"),
    )
    view = member_refresh_view(full)
    for name in PROGRESS:
        assert getattr(view, name) == getattr(full, name)
    defaults = RefreshSnapshot()
    for name in set(RefreshSnapshot.model_fields) - PROGRESS:
        assert getattr(view, name) == getattr(defaults, name), name


def test_refresh_routes_hide_the_log_from_members(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    with session_client(path) as client:
        service = client.app.state.refresh_service  # type: ignore[attr-defined]
        service.emit("Generic progress line.")
        admin = client.get("/api/refresh").json()
        assert admin["logs"]
        streamed = client.get("/api/refresh/events?once=true").text
        assert "Generic progress line." in streamed
        store = SessionStore(path)
        now = int(time.time())
        cookie = store.issue(
            store.prepare(None), "member-uid", now + 300, auth_time=now
        ).cookie
        client.cookies.clear()
        client.cookies.set(COOKIE, cookie, domain="localhost.local", path="/api")
        member = client.get("/api/refresh").json()
        assert member["logs"] == []
        assert member["status"] == admin["status"]
        streamed = client.get("/api/refresh/events?once=true").text
        assert "Generic progress line." not in streamed
