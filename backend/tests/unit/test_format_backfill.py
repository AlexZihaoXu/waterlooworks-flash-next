"""Gradual description-formatting backfill inside the detail queue.

Generic IDs and structural content written through the production storage
functions on real migrations; no network, no WaterlooWorks requests.
"""

import ast
import inspect
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alert_support import collect, first_seen, structural_content

from waterlooworks_flash_next.content import StoredPostingFormatting
from waterlooworks_flash_next.content_storage import (
    complete_content_attempt,
    fail_content_attempt,
    record_content_request,
    start_content_attempt,
)
from waterlooworks_flash_next.database import (
    StoredListing,
    open_database,
    save_listings,
    start_collection_run,
)
from waterlooworks_flash_next.refresh import (
    DEFAULT_FORMAT_BACKFILL_PER_RUN,
    FORMAT_BACKFILL_RESERVE,
    RefreshService,
    configured_format_backfill,
    format_backfill_allowance,
    format_backfill_candidates,
    plan_detail_queue,
)
from waterlooworks_flash_next.ww_pacing import PacingConfig, RequestGate

FORMATTING = StoredPostingFormatting.model_validate(
    {
        "fields": {
            "Job Summary:": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "Structural", "bold": True}],
                }
            ]
        }
    }
)


def test_backfill_setting_is_strict_server_configuration() -> None:
    assert DEFAULT_FORMAT_BACKFILL_PER_RUN == 20
    assert configured_format_backfill(None) == 20
    assert configured_format_backfill("0") == 0
    assert configured_format_backfill("100") == 100
    for value in ("101", "-1", "", " 5", "5 ", "1e2", "٣", "0x10", "1000"):
        with pytest.raises(ValueError, match="WW_FORMAT_BACKFILL_PER_RUN") as refused:
            configured_format_backfill(value)
        assert "from 0 to 100" in str(refused.value)


def test_allowance_keeps_the_rolling_reserve_after_expected_requests() -> None:
    assert FORMAT_BACKFILL_RESERVE == 300

    def allowance(
        daily: int, queued: int = 0, *, per_run: int = 20, run: int = 400
    ) -> int:
        return format_backfill_allowance(
            per_run, 50, daily_remaining=daily, run_remaining=run, queued=queued
        )

    # 2 overhead requests: bootstrap metadata read and a possible saved lead.
    assert allowance(1000) == 20
    assert allowance(322) == 20  # 322 - 2 - 20 = 300 left
    assert allowance(321) == 19
    assert allowance(302) == 0
    assert allowance(200) == 0
    assert allowance(1000, queued=690, run=2000) == 8  # new postings first
    assert allowance(1000, queued=700, run=2000) == 0
    assert allowance(1000, per_run=0) == 0
    assert allowance(1000, run=30, queued=20) == 8  # never trips the run cap
    assert (
        format_backfill_allowance(
            20, 3, daily_remaining=1000, run_remaining=400, queued=0
        )
        == 3
    )


def test_gate_reports_daily_and_run_remaining_separately() -> None:
    gate = RequestGate(PacingConfig(request_cap=400), daily_remaining=900)
    gate.note_external(0.0, 10)
    assert (gate.daily_remaining, gate.run_remaining, gate.remaining) == (
        890,
        390,
        390,
    )
    tight = RequestGate(PacingConfig(request_cap=400), daily_remaining=100)
    assert (tight.daily_remaining, tight.run_remaining, tight.remaining) == (
        100,
        400,
        100,
    )


def _listed(path: Path, *ids: str) -> tuple[str, list[StoredListing]]:
    listings = [StoredListing(id=pid, title="Title") for pid in ids]
    connection = open_database(path)
    try:
        run_id = start_collection_run(connection)
        save_listings(connection, run_id, listings)
    finally:
        connection.close()
    return run_id, listings


def _format(path: Path, run_id: str, posting_id: str) -> None:
    connection = open_database(path)
    try:
        attempt = start_content_attempt(connection, run_id, posting_id)
        record_content_request(connection, attempt, retry=False)
        complete_content_attempt(
            connection, attempt, structural_content(posting_id), FORMATTING
        )
    finally:
        connection.close()


def _aged(path: Path, days: dict[str, int]) -> None:
    now = datetime.now(UTC)
    for posting_id, age in days.items():
        first_seen(path, posting_id, now - timedelta(days=age))


def test_candidates_are_saved_postings_missing_current_formatting(
    tmp_path: Path,
) -> None:
    path = tmp_path / "db.sqlite3"
    run_id, listings = _listed(path, "1", "2", "3", "4", "5")
    collect(path, run_id, "1", "2", "3")
    _format(path, run_id, "3")
    _aged(path, {"1": 5, "2": 1, "3": 1, "4": 1, "5": 1})
    connection = open_database(path)
    try:
        # 4 and 5 were never collected; 3 already has current formatting.
        found = format_backfill_candidates(connection, listings, set())
        assert [item.id for item in found] == ["2", "1"]  # newest sighting first
        assert format_backfill_candidates(connection, listings, {"2"})[0].id == "1"
        # Formatting from an older format version or content hash is missing.
        with connection:
            connection.execute(
                "UPDATE posting_content_formatting SET format_version='rich-v0'"
            )
        assert {
            item.id for item in format_backfill_candidates(connection, listings, set())
        } == {"1", "2", "3"}
    finally:
        connection.close()


def test_posting_failing_validation_on_this_revision_is_not_backfilled(
    tmp_path: Path,
) -> None:
    path = tmp_path / "db.sqlite3"
    run_id, listings = _listed(path, "1", "2")
    collect(path, run_id, "1", "2")
    connection = open_database(path)
    try:
        attempt = start_content_attempt(connection, run_id, "1")
        record_content_request(connection, attempt, retry=False)
        fail_content_attempt(connection, attempt, "response_schema")
        found = format_backfill_candidates(connection, listings, set())
        assert [item.id for item in found] == ["2"]
    finally:
        connection.close()


def test_plan_puts_backfill_after_every_never_collected_posting(
    tmp_path: Path,
) -> None:
    path = tmp_path / "db.sqlite3"
    run_id, listings = _listed(path, "1", "2", "3", "4", "5", "6")
    collect(path, run_id, "1", "2", "3")
    _aged(path, {"1": 9, "2": 3, "3": 6, "4": 2, "5": 1, "6": 4})
    connection = open_database(path)
    try:
        plan = plan_detail_queue(
            connection,
            listings,
            ["5"],
            detail_limit=None,
            full_refetch=False,
            backfill_per_run=2,
            daily_remaining=1000,
            run_remaining=400,
        )
    finally:
        connection.close()
    # Visible new posting leads; new postings newest first; then backfill.
    assert [item.id for item in plan.queue] == ["5", "4", "6", "2", "3"]
    assert (plan.backfill_missing, plan.backfill_queued, plan.backfill_deferred) == (
        3,
        2,
        0,
    )
    assert plan.limited is False


def test_backfill_only_run_keeps_a_visible_bootstrap_lead(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    run_id, listings = _listed(path, "1", "2", "3")
    collect(path, run_id, "1", "2", "3")
    _format(path, run_id, "3")
    _aged(path, {"1": 1, "2": 2, "3": 3})
    connection = open_database(path)
    try:
        visible_backfill = plan_detail_queue(
            connection,
            listings,
            ["2"],
            detail_limit=None,
            full_refetch=False,
            backfill_per_run=20,
            daily_remaining=1000,
            run_remaining=400,
        )
        saved_lead = plan_detail_queue(
            connection,
            listings,
            ["3"],
            detail_limit=None,
            full_refetch=False,
            backfill_per_run=20,
            daily_remaining=1000,
            run_remaining=400,
        )
    finally:
        connection.close()
    assert [item.id for item in visible_backfill.queue] == ["2", "1"]
    # No queued posting is visible: an already-saved visible one leads.
    assert [item.id for item in saved_lead.queue] == ["3", "1", "2"]


def test_plan_backfills_nothing_without_the_reserve(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    run_id, listings = _listed(path, "1", "2", "3")
    collect(path, run_id, "1", "2")
    connection = open_database(path)
    try:
        for per_run, daily in ((20, 303), (0, 1000)):
            plan = plan_detail_queue(
                connection,
                listings,
                ["3"],
                detail_limit=None,
                full_refetch=False,
                backfill_per_run=per_run,
                daily_remaining=daily,
                run_remaining=400,
            )
            assert [item.id for item in plan.queue] == ["3"]
            assert plan.backfill_queued == 0
            assert plan.backfill_deferred == (2 if per_run else 0)
    finally:
        connection.close()


def test_detail_limit_and_full_refetch_bound_the_backfill(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    run_id, listings = _listed(path, "1", "2", "3")
    collect(path, run_id, "1", "2")
    connection = open_database(path)
    try:
        limited = plan_detail_queue(
            connection,
            listings,
            ["3"],
            detail_limit=2,
            full_refetch=False,
            backfill_per_run=20,
            daily_remaining=1000,
            run_remaining=400,
        )
        refetch = plan_detail_queue(
            connection,
            listings,
            ["3"],
            detail_limit=None,
            full_refetch=True,
            backfill_per_run=20,
            daily_remaining=1000,
            run_remaining=400,
        )
    finally:
        connection.close()
    assert len(limited.queue) == 2 and limited.backfill_queued == 1
    assert limited.limited is False  # every new posting was still requested
    assert sorted(item.id for item in refetch.queue) == ["1", "2", "3"]
    assert refetch.backfill_missing == 0


def test_service_plans_with_the_gate_budget_and_logs_fixed_counts(
    tmp_path: Path,
) -> None:
    assert RefreshService(tmp_path / "db.sqlite3", None).format_backfill == 20
    assert (
        RefreshService(tmp_path / "db.sqlite3", None, format_backfill=0).format_backfill
        == 0
    )
    source = textwrap.dedent(inspect.getsource(RefreshService._run))
    call = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "plan_detail_queue"
    )
    keywords = {k.arg: ast.unparse(k.value) for k in call.keywords}
    assert keywords["daily_remaining"] == "gate.daily_remaining"
    assert keywords["run_remaining"] == "gate.run_remaining"
    assert keywords["backfill_per_run"] == "self.format_backfill"
    assert "Description formatting backfill:" in source
