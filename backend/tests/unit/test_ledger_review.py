"""Operator review of unknown-usage attempts; ledger metadata only."""

import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from waterlooworks_flash_next.database import open_database
from waterlooworks_flash_next.openai_ledger import (
    AttemptLedger,
    BudgetLimits,
    BudgetStopped,
    resolve_unknown,
    unknown_attempts,
)


def _unknown(path: Path) -> tuple[str, str]:
    run_id = str(uuid4())
    ledger = AttemptLedger(path)
    ledger.create_run(run_id, BudgetLimits(request_limit=10))
    return run_id, ledger.reserve(run_id, "a" * 64)


def test_operator_lists_and_resolves_unknown_attempts(tmp_path: Path) -> None:
    path = tmp_path / "empty.sqlite3"
    open_database(path).close()
    run_id, attempt = _unknown(path)
    ledger = AttemptLedger(path)
    with pytest.raises(BudgetStopped):
        ledger.require_resumable(run_id)
    listed = unknown_attempts(path)
    assert [(row.attempt_id, row.run_id, row.finalized) for row in listed] == [
        (attempt, run_id, False)
    ]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "waterlooworks_flash_next.openai_ledger",
            str(path),
            "unknown",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.splitlines() == [
        f"attempt {attempt} run {run_id} reserved {listed[0].reserved_at} finalized no"
    ]
    resolve_unknown(path, attempt, Decimal("0.0123"))
    ledger.require_resumable(run_id)
    assert unknown_attempts(path) == []
    row = (
        ledger._connect()
        .execute(
            "SELECT known_usd,pricing_version FROM openai_attempts WHERE attempt_id=?",
            (attempt,),
        )
        .fetchone()
    )
    assert row == ("0.0123", "operator-reviewed")
    with pytest.raises(ValueError):
        resolve_unknown(path, attempt, Decimal("1"))  # already known
    with pytest.raises(ValueError):
        resolve_unknown(path, str(uuid4()), Decimal("1"))
    with pytest.raises(ValueError):
        resolve_unknown(path, _unknown(path)[1], Decimal("-1"))


def test_cli_resolve_rejects_invalid_amounts_without_echo(tmp_path: Path) -> None:
    path = tmp_path / "empty.sqlite3"
    open_database(path).close()
    _, attempt = _unknown(path)
    command = [
        sys.executable,
        "-m",
        "waterlooworks_flash_next.openai_ledger",
        str(path),
        "resolve",
        attempt,
    ]
    bad = subprocess.run(
        [*command, "--known-usd", "NaN-marker"], capture_output=True, text=True
    )
    assert bad.returncode != 0 and "NaN-marker" not in bad.stdout + bad.stderr
    good = subprocess.run(
        [*command, "--known-usd", "0"], capture_output=True, text=True, check=True
    )
    assert good.stdout.strip() == f"attempt {attempt} resolved"
    assert unknown_attempts(path) == []
