"""Consistent ledger reads while a real second connection commits a reservation."""

import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from waterlooworks_flash_next.database import open_database
from waterlooworks_flash_next.openai_ledger import AttemptLedger, BudgetLimits
from waterlooworks_flash_next.openai_usage import PRICING_DATE, CostSummary
from waterlooworks_flash_next.openai_usage_api import read_usage


def test_usage_summary_and_counters_share_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "empty.sqlite3"
    open_database(path).close()
    ledger = AttemptLedger(path)
    run_id = str(uuid4())
    ledger.create_run(run_id, BudgetLimits())
    original = AttemptLedger._summary

    def concurrent_insert(connection: sqlite3.Connection, run: str) -> CostSummary:
        summary = original(connection, run)
        other = ledger._connect()
        try:
            with other:
                other.execute(
                    "INSERT INTO openai_attempts "
                    "(attempt_id,run_id,operation_hash,reserved_at,pricing_version) "
                    "VALUES(?,?,?,?,?)",
                    (str(uuid4()), run, "0" * 64, "2026-09-09T00:00:00Z", PRICING_DATE),
                )
        finally:
            other.close()
        return summary

    with monkeypatch.context() as patch:
        patch.setattr(AttemptLedger, "_summary", staticmethod(concurrent_insert))
        observed = read_usage(path).runs[0]
    assert observed.attempts == 0
    assert observed.unpriced_attempts == 0
    assert observed.total_usd == "0"
    assert observed.input_tokens == 0
    assert observed.cached_tokens == 0
    assert observed.cache_write_tokens == 0
    assert observed.output_tokens == 0
    assert observed.reasoning_tokens == 0

    next_read = read_usage(path).runs[0]
    assert next_read.attempts == 1
    assert next_read.unpriced_attempts == 1
    assert next_read.total_usd is None
    assert next_read.input_tokens is None
