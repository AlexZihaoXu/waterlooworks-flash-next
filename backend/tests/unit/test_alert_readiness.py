"""When a saved posting is ready to appear in a Discord new-job alert.

Real migrated SQLite with structural rows only; nothing contacts a provider.
"""

from pathlib import Path

import pytest
from alert_support import analysed, collect

from waterlooworks_flash_next.ai_settings import AIConfig, AISettingsStore
from waterlooworks_flash_next.alert_readiness import (
    ai_extraction_active,
    ready_posting_ids,
)
from waterlooworks_flash_next.database import (
    StoredListing,
    open_database,
    save_listings,
    start_collection_run,
)


@pytest.fixture
def path(tmp_path: Path) -> Path:
    database = tmp_path / "ready.sqlite3"
    open_database(database).close()
    return database


def listed(path: Path, *ids: str) -> str:
    connection = open_database(path)
    run_id = start_collection_run(connection)
    save_listings(connection, run_id, [StoredListing(id=pid) for pid in ids])
    connection.close()
    return run_id


def ready(path: Path, *, ai_active: bool) -> set[str]:
    connection = open_database(path)
    try:
        return ready_posting_ids(connection, ai_active=ai_active)
    finally:
        connection.close()


def test_without_ai_collected_detail_content_alone_is_ready(path: Path) -> None:
    run_id = listed(path, "1", "2")
    assert ready(path, ai_active=False) == set()
    collect(path, run_id, "1")
    assert ready(path, ai_active=False) == {"1"}


def test_content_from_an_older_extractor_is_not_ready(path: Path) -> None:
    run_id = listed(path, "1")
    collect(path, run_id, "1")
    connection = open_database(path)
    with connection:
        connection.execute("UPDATE posting_contents SET extractor_version='old'")
    connection.close()
    assert ready(path, ai_active=False) == set()


@pytest.mark.parametrize(
    ("status", "category", "expected"),
    [
        ("accepted", None, True),
        ("rejected", "evidence_rejected", True),
        ("failed", "http_server", True),
        ("not_needed", None, True),
        ("not_needed", "stopped", False),  # deferred by postings_per_run
        ("interrupted", "stopped", False),
        ("pending", None, False),
        ("running", None, False),
    ],
)
def test_with_ai_the_current_content_needs_a_terminal_extraction(
    path: Path, status: str, category: str | None, expected: bool
) -> None:
    run_id = listed(path, "1")
    collect(path, run_id, "1")
    assert ready(path, ai_active=True) == set()
    analysed(path, "1", status, error_category=category)
    assert ready(path, ai_active=True) == ({"1"} if expected else set())
    assert ready(path, ai_active=False) == {"1"}


def test_extraction_of_superseded_content_does_not_count(path: Path) -> None:
    run_id = listed(path, "1")
    collect(path, run_id, "1")
    analysed(path, "1", "rejected", error_category="evidence_rejected")
    collect(path, run_id, "1", summary="Changed")
    assert ready(path, ai_active=True) == set()
    analysed(path, "1", "not_needed")
    assert ready(path, ai_active=True) == {"1"}


def test_ai_activation_reads_the_saved_active_configuration(path: Path) -> None:
    assert ai_extraction_active(path) is False
    assert ai_extraction_active(path.parent / "missing.sqlite3") is False
    store = AISettingsStore(path)
    store.save_draft(AIConfig(model_id="gpt-5.6-luna"))
    assert ai_extraction_active(path) is False  # a draft is not activation
    connection = open_database(path)
    with connection:
        connection.execute(
            "UPDATE ai_settings SET active_json=draft_json,activated_at=updated_at"
        )
    connection.close()
    assert ai_extraction_active(path) is True
