"""Empty-database and primitive contracts; never seed invented postings."""

from pathlib import Path

from auth_support import session_client

from waterlooworks_flash_next.database import open_database


def test_keyword_index_empty_conditional_get_is_read_only(tmp_path: Path) -> None:
    path = tmp_path / "empty.sqlite3"
    with session_client(path) as client:
        first = client.get("/api/keyword-index")
        assert first.status_code == 200
        assert first.json()["entries"] == []
        assert first.json()["run_id"] is None
        assert first.json()["total_postings"] == 0
        assert len(first.json()["revision"]) == 64
        # The handler's revalidation policy is no longer overwritten.
        assert first.headers["cache-control"] == "private, no-cache"
        second = client.get(
            "/api/keyword-index", headers={"If-None-Match": first.headers["etag"]}
        )
        assert second.status_code == 304
        assert second.content == b""
        assert second.headers["etag"] == first.headers["etag"]
        assert second.headers["cache-control"] == "private, no-cache"
    connection = open_database(path)
    try:
        for table in (
            "collection_runs",
            "source_extraction_runs",
            "source_extractions",
        ):
            assert (
                connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
            )
    finally:
        connection.close()
