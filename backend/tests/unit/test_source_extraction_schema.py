"""Empty-schema contracts; no postings or model responses are fabricated."""

from pathlib import Path

from waterlooworks_flash_next.database import open_database


def test_source_extraction_schema_is_additive_and_empty(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "empty.sqlite3")
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 10
        for name in (
            "source_extraction_runs",
            "source_extraction_items",
            "source_extractions",
            "source_extraction_terms",
            "source_extraction_pay",
        ):
            assert connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0] == 0
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        triggers = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }
        for name in (
            "source_extractions",
            "source_extraction_terms",
            "source_extraction_pay",
        ):
            assert f"{name}_no_update" in triggers
            assert f"{name}_no_delete" in triggers
    finally:
        connection.close()
