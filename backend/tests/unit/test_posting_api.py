"""Read-only detail transport against a real empty database; no posting fixtures."""

from pathlib import Path

from auth_support import session_client

from waterlooworks_flash_next.database import open_database


def test_missing_posting_and_strict_id_leave_database_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    with session_client(path) as client:
        connection = open_database(path)
        before = list(connection.iterdump())
        for posting_id in ("0", "00012", "999999999999999999999999"):
            response = client.get(f"/api/postings/{posting_id}")
            assert response.status_code == 404
            assert response.headers["cache-control"] == "no-store"
        for posting_id in ("-1", "1.0", "abc", "1%20OR%201=1", "%EF%BC%91"):
            response = client.get(f"/api/postings/{posting_id}")
            assert response.status_code == 422
            assert posting_id not in response.json()["detail"]
        assert (
            client.get("/api/postings/1", headers={"host": "example.org"}).status_code
            == 403
        )
        assert list(connection.iterdump()) == before
        connection.close()
