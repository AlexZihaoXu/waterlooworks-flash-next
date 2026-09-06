"""Real local API/SQLite checks; no mock postings, auth, or external requests."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from waterlooworks_flash_next.api import create_app
from waterlooworks_flash_next.database import open_database
from waterlooworks_flash_next.refresh import RefreshConflict, claim_refresh


def test_empty_workspace_and_sse_are_real_responses(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "db.sqlite3")) as client:
        assert client.get("/api/health").json()["schema_version"] == 3
        assert client.get("/doc").status_code == 200
        spec = client.get("/doc/openapi.json").json()
        assert spec["paths"]["/api/refresh"]["post"]["operationId"] == "startRefresh"
        assert "RefreshSnapshot" in spec["components"]["schemas"]
        response = client.get("/api/workspace")
        assert response.status_code == 200
        assert response.json()["postings"] == []
        assert response.json()["manual_refresh_available"] is False
        events = client.get("/api/refresh/events?once=true")
        assert events.status_code == 200
        assert "text/event-stream" in events.headers["content-type"]
        assert "event: refresh" in events.text
        assert '"status":"idle"' in events.text
        assert client.post("/api/refresh", json={}).status_code == 503


def test_cross_origin_refresh_and_form_posts_are_rejected(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "db.sqlite3")) as client:
        assert (
            client.post(
                "/api/refresh", json={}, headers={"origin": "https://example.org"}
            ).status_code
            == 403
        )
        assert client.post("/api/refresh", content="").status_code == 415


def test_refresh_claim_blocks_concurrency_and_enforces_cooldown(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    open_database(path).close()
    run_id = claim_refresh(path)
    with pytest.raises(RefreshConflict) as busy:
        claim_refresh(path)
    assert busy.value.status_code == 409
    connection = open_database(path)
    with connection:
        connection.execute(
            "UPDATE collection_runs SET status='failed', finished_at=started_at, "
            "error_category='internal' WHERE id=?",
            (run_id,),
        )
    connection.close()
    with pytest.raises(RefreshConflict) as cooldown:
        claim_refresh(path)
    assert cooldown.value.status_code == 429
    assert cooldown.value.retry_after > 0
