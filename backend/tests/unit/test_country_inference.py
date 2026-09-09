"""Country contracts on primitives and empty databases only."""

from pathlib import Path

from waterlooworks_flash_next.api_models import CountryInference
from waterlooworks_flash_next.country_inference import read_country_inferences
from waterlooworks_flash_next.country_schema import COUNTRIES
from waterlooworks_flash_next.database import SCHEMA_VERSION, open_database


def test_country_schema_empty_and_additive(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "empty.sqlite3")
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert read_country_inferences(connection) == {}
        assert read_country_inferences(connection, "0") == {}
        assert (
            connection.execute("SELECT count(*) FROM country_codes").fetchone()[0]
            == 249
        )
        assert connection.execute("SELECT count(*) FROM postings").fetchone()[0] == 0
        triggers = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }
        assert {
            "country_inference_terminal_no_update",
            "country_inference_source_guard",
            "country_inference_item_identity_no_update",
            "country_inference_identity_no_update",
        } <= triggers
        for table in ("country_inference_runs", "country_inference_items"):
            assert (
                connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
            )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        connection.close()


def test_country_unknown_contract_has_no_default_guess() -> None:
    value = CountryInference()
    assert value.state == "not_requested"
    assert value.provenance == "model_inference"
    assert value.country_code is None and value.country_name is None
    assert value.confidence is None and value.evidence == []
    assert len(COUNTRIES) == 249
    assert COUNTRIES["CA"] == "Canada"
    assert "ZZ" not in COUNTRIES and "UK" not in COUNTRIES
