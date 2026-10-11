"""Dry-run evaluation: fixed-category output, no database writes, no content.

The scripted Python sidecar stand-in issues the same RPCs as the Node sidecar;
fields are synthetic strings, never saved postings.
"""

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet

from waterlooworks_flash_next import pi_bridge, pi_eval
from waterlooworks_flash_next.ai_custom import ValidatedEndpoint
from waterlooworks_flash_next.ai_settings import AIConfig, AISettingsStore
from waterlooworks_flash_next.database import open_database
from waterlooworks_flash_next.inference import _Item
from waterlooworks_flash_next.inference_prompt import PiPrompt
from waterlooworks_flash_next.pi_bridge import PiSession

FAKE = Path(__file__).with_name("fake_sidecar.py")
PAY = "Pay starts at USD 25 per hour for 35 hours/week"
SECRET_TEXT = "Pay starts"
CUSTOM = AIConfig(
    access_mode="custom",
    model_id="local-model",
    custom_base_url="http://model.example:8000/v1",
    custom_context_window=32768,
    reasoning_effort="medium",
)
ENDPOINT = ValidatedEndpoint(
    "http://model.example:8000/v1", "http", "model.example", 8000, "/v1", "100.64.0.10"
)
USAGE = {"input": 400, "output": 50, "cache_read": 0, "cache_write": 0, "reasoning": 30}


@pytest.fixture
def path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WW_CREDENTIALS_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(
        pi_bridge,
        "sidecar_command",
        lambda _dir=None: ([sys.executable, "-I", str(FAKE)], FAKE.parent),
    )
    database = tmp_path / "empty.sqlite3"
    open_database(database).close()
    return database


def _rate(evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "bound_type": "minimum",
        "minimum": "25",
        "maximum": None,
        "minimum_inclusive": True,
        "maximum_inclusive": None,
        "currency": "USD",
        "period": "hour",
        "hours_per_week": "35",
        "payout_frequency": None,
        "eligibility": None,
        "evidence": evidence,
        "eligibility_evidence": None,
    }


def _item(
    steps: list[dict[str, Any]], status: str = "submitted", **extra: Any
) -> _Item:
    script = json.dumps({"steps": steps, "status": status, **extra})
    prompt = PiPrompt(
        "system", script, (("Compensation and Benefits:", PAY),), {"type": "object"}
    )
    return _Item(
        "eval",
        "1",
        "a" * 64,
        "b" * 64,
        {"Compensation and Benefits:": PAY},
        prompt,
        True,
        False,
        (),
    )


def _turn(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"method": "ledger.reserve", "params": {"item_id": "", "turn": 1}},
        {
            "method": "ledger.finalize",
            "params": {
                "attempt": "$last",
                "outcome": "completed",
                "http_status": 200,
                "model": "local-model",
                "usage": USAGE,
                "cost_usd": None,
            },
        },
        {"method": "submission.check", "params": {"item_id": "", "payload": payload}},
    ]


def test_rows_are_fixed_categories_with_token_metrics_and_no_content(
    path: Path,
) -> None:
    good = {
        "base_rates": [
            _rate(
                {
                    "field": "Compensation and Benefits:",
                    "start": 0,
                    "end": len(PAY),
                    "text": None,
                }
            )
        ],
        "skills": [],
    }
    bad = {
        "base_rates": [
            _rate(
                {
                    "field": "Compensation and Benefits:",
                    "start": None,
                    "end": None,
                    "text": "USD 99",
                }
            )
        ],
        "skills": [],
    }
    items = [
        _item(_turn(good), submit=good),
        _item(_turn(bad), status="failed", category="evidence_rejected"),
    ]
    output: list[str] = []
    with PiSession(
        path, CUSTOM, "run", endpoint=ENDPOINT, ledger=pi_eval.MemoryLedger()
    ) as session:
        rows = pi_eval.run_eval(items, session, 1, emit=output.append)
    assert [(r.outcome, r.category, r.reason) for r in rows] == [
        ("accepted", "", ""),
        ("rejected", "evidence_rejected", "evidence_not_found"),
    ]
    assert rows[0].input_tokens == 400 and rows[0].reasoning_tokens == 30
    assert all(
        set(json.loads(line)) == set(pi_eval.EvalRow.__dataclass_fields__)
        for line in output
    )
    printed = "\n".join(output)
    assert (
        SECRET_TEXT not in printed and "USD 99" not in printed and '"1"' not in printed
    )
    summary = pi_eval.summarize(rows, CUSTOM, 3.0)["summary"]
    assert summary["attempted"] == 2 and summary["accepted"] == 1
    assert summary["acceptance_rate"] == 0.5
    assert summary["reasons"] == {"evidence_not_found": 1}
    assert summary["reasoning_tokens_total"] == 60
    assert summary["reasoning"] == "medium"


def test_main_is_read_only_and_applies_overrides(
    path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = AISettingsStore(path)
    store.save_draft(
        CUSTOM.model_copy(update={"custom_base_url": "http://100.64.0.10:8000/v1"})
    )
    store.activate()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pi_eval",
            str(path),
            "--limit",
            "5",
            "--reasoning",
            "low",
            "--max-turns",
            "4",
        ],
    )
    pi_eval.main()
    lines = capsys.readouterr().out.splitlines()
    summary = json.loads(lines[-1])["summary"]
    assert summary["attempted"] == 0
    assert (summary["reasoning"], summary["max_turns"]) == ("low", 4)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    for wal in path.parent.glob(path.name + "-*"):
        assert wal.stat().st_size == 0 or wal.name.endswith("-shm")


def test_memory_ledger_refuses_duplicate_operations() -> None:
    from waterlooworks_flash_next.openai_ledger import BudgetStopped

    ledger = pi_eval.MemoryLedger()
    assert ledger.reserve("run", "op") != ledger.reserve("run", "op2")
    with pytest.raises(BudgetStopped):
        ledger.reserve("run", "op")


def test_v9_baseline_uses_exact_text_and_the_generic_hint(path: Path) -> None:
    sloppy = {
        "base_rates": [
            _rate(
                {
                    "field": "Compensation and Benefits:",
                    "text": "pay starts at usd 25 per hour for 35 hours/week",
                }
            )
        ],
        "skills": [],
    }
    with PiSession(
        path, CUSTOM, "run", endpoint=ENDPOINT, ledger=pi_eval.MemoryLedger()
    ) as session:
        baseline = pi_eval.evaluate_item(
            session, 0, _item(_turn(sloppy), submit=sloppy), "v9"
        )
        current = pi_eval.evaluate_item(
            session, 1, _item(_turn(sloppy), submit=sloppy), "v10"
        )
    # Exact matching rejects a case-only difference; v10 maps it to the span.
    assert (baseline.outcome, baseline.reason) == ("rejected", "evidence_not_unique")
    assert current.outcome == "accepted"
