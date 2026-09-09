"""Empty-database refresh analysis and progress contracts, without provider fixtures."""

from pathlib import Path

from waterlooworks_flash_next.api_models import RefreshSnapshot
from waterlooworks_flash_next.database import open_database
from waterlooworks_flash_next.refresh import RefreshService


def test_empty_workspace_analysis_does_not_create_model_work(tmp_path: Path) -> None:
    path = tmp_path / "empty.sqlite3"
    open_database(path).close()
    service = RefreshService(path, None)
    service._analyze_saved_data("empty-check")
    state = service.snapshot()
    assert state.analysis_total == 0
    assert state.analysis_completed == 0
    assert any("No saved detail content" in log.message for log in state.logs)
    with open_database(path) as connection:
        assert (
            connection.execute("SELECT count(*) FROM inference_jobs").fetchone()[0] == 0
        )


def test_analysis_snapshot_starts_with_empty_counters() -> None:
    state = RefreshSnapshot()
    assert state.analysis_job_id is None
    assert state.analysis_total == state.analysis_completed == 0
    assert state.analysis_accepted == state.analysis_rejected == 0
    assert (
        state.analysis_failed
        == state.analysis_skipped
        == state.analysis_cache_hits
        == 0
    )
