"""Normalized v7 contracts on real empty databases and value primitives only."""

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from waterlooworks_flash_next.compact_history import migrate_compact_history
from waterlooworks_flash_next.content_schema import migrate_content
from waterlooworks_flash_next.database import (
    _SCHEMA,
    SCHEMA_VERSION,
    DatabaseError,
    _migrate_history,
    open_database,
)
from waterlooworks_flash_next.detail_schema import migrate_details
from waterlooworks_flash_next.normalized_storage import (
    NormalizationError,
    decimal_from_storage,
    decimal_to_storage,
    normalize_saved_posting,
    normalize_saved_postings,
)
from waterlooworks_flash_next.observations import migrate_observations


def test_v7_empty_schema_is_explicit_indexed_and_does_not_analyze(
    tmp_path: Path,
) -> None:
    connection = open_database(tmp_path / "normalized.sqlite3")
    assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    expected = {
        "analysis_projections",
        "posting_projection_current",
        "normalized_fixed_fields",
        "normalized_field_evidence",
        "normalized_levels",
        "normalized_targeted_disciplines",
        "normalized_skills",
        "normalized_skill_mentions",
        "normalized_compensation_rates",
        "normalized_application_instructions",
        "normalized_application_links",
    }
    assert expected <= tables
    assert all(
        connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        for table in expected
    )
    indexes = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
    }
    assert {
        "idx_projection_source",
        "idx_fixed_arrangement",
        "idx_level_value",
        "idx_skill_mentions_skill",
        "idx_compensation_query",
        "idx_application_link_class",
    } <= indexes
    triggers = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        )
    }
    assert {
        "projection_source_identity",
        "current_projection_insert",
        "current_projection_update",
        "deterministic_only_normalized_fixed_fields",
        "deterministic_only_normalized_projection_results",
        "source_identity_normalized_compensation_rates",
    } <= triggers
    assert "deterministic_only_normalized_compensation_rates" not in triggers
    assert "deterministic_only_normalized_skill_mentions" not in triggers
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    connection.close()


def test_v7_migration_failure_rolls_back_to_empty_v6(tmp_path: Path) -> None:
    path = tmp_path / "v6.sqlite3"
    connection = sqlite3.connect(path)
    with connection:
        for statement in _SCHEMA:
            connection.execute(statement)
        _migrate_history(connection)
        migrate_details(connection)
        migrate_content(connection)
        migrate_observations(connection)
        migrate_compact_history(connection)
        connection.execute("PRAGMA user_version=6")
        connection.execute("CREATE TABLE analysis_projections(collision INTEGER)")
    connection.close()

    with pytest.raises(DatabaseError, match="initialization failed"):
        open_database(path)
    with sqlite3.connect(path) as failed:
        assert failed.execute("PRAGMA user_version").fetchone()[0] == 6
        assert [
            row[1] for row in failed.execute("PRAGMA table_info(analysis_projections)")
        ] == ["collision"]
        assert not failed.execute(
            "SELECT name FROM sqlite_master WHERE name='idx_revision_identity'"
        ).fetchall()


def test_empty_offline_batch_and_missing_posting_are_bounded(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "normalized.sqlite3")
    result = normalize_saved_postings(connection)
    assert result.postings_seen == 0
    assert result.projections_created == 0
    with pytest.raises(NormalizationError, match="source revisions"):
        normalize_saved_posting(connection, "404")
    assert (
        connection.execute("SELECT count(*) FROM analysis_projections").fetchone()[0]
        == 0
    )
    connection.close()


@pytest.mark.parametrize(
    ("value", "stored"),
    [
        (Decimal("0"), "0"),
        (Decimal("-0"), "0"),
        (Decimal("20.00"), "20"),
        (Decimal("0.1250"), "0.125"),
        (Decimal("12345678901234567890.01"), "12345678901234567890.01"),
    ],
)
def test_decimal_storage_is_exact_and_canonical(value: Decimal, stored: str) -> None:
    assert decimal_to_storage(value) == stored
    assert decimal_from_storage(stored) == value


@pytest.mark.parametrize(
    "value",
    [Decimal("NaN"), Decimal("Infinity"), Decimal("-1"), Decimal("1e100")],
)
def test_decimal_storage_rejects_unsupported_values(value: Decimal) -> None:
    with pytest.raises(NormalizationError, match="Decimal"):
        decimal_to_storage(value)
