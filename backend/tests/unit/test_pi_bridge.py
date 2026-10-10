"""Backend side of the sidecar protocol, against a scripted Python stand-in.

The stand-in issues RPCs exactly as the Node sidecar does, so these tests cover
strict parsing, credential locking/rotation, per-turn ledger reservation and
finalization, tool and submission RPCs, environment scrubbing and the private
HOME lifecycle without Node or any network access.
"""

import json
import secrets
import sqlite3
import sys
import time
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from waterlooworks_flash_next import pi_bridge
from waterlooworks_flash_next.ai_custom import ValidatedEndpoint
from waterlooworks_flash_next.ai_settings import (
    AIConfig,
    AISettingsStore,
    CustomToken,
    OAuthCredential,
)
from waterlooworks_flash_next.database import open_database
from waterlooworks_flash_next.openai_ledger import AttemptLedger, BudgetLimits
from waterlooworks_flash_next.openai_settings import OpenAIKey, OpenAISettingsStore
from waterlooworks_flash_next.pi_bridge import PiItem, PiSession, SidecarError

FAKE = Path(__file__).with_name("fake_sidecar.py")
ENDPOINT = ValidatedEndpoint(
    base_url="http://model.example:8000/v1",
    scheme="http",
    host="model.example",
    port=8000,
    path="/v1",
    address="100.64.0.10",
)
CUSTOM = AIConfig(
    access_mode="custom",
    model_id="local-model",
    custom_base_url="http://model.example:8000/v1",
    custom_context_window=32768,
)
USAGE = {"input": 100, "output": 20, "cache_read": 10, "cache_write": 0, "reasoning": 5}


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


def _session(path: Path, config: AIConfig = CUSTOM) -> tuple[PiSession, str]:
    run_id = str(uuid4())
    AttemptLedger(path).create_run(run_id, BudgetLimits(request_limit=100))
    endpoint = ENDPOINT if config.access_mode == "custom" else None
    return PiSession(path, config, run_id, endpoint=endpoint).start(), run_id


def _item(steps: list[dict[str, object]], **script: object) -> PiItem:
    return PiItem(
        item_id=f"x-{secrets.randbelow(10**9)}",
        kind="extraction",
        system="system",
        user=json.dumps({"steps": steps, **script}),
        sections=(("Compensation and Benefits:", "$25 CAD per hour"),),
        submit_schema={"type": "object"},
        operation_key=secrets.token_hex(8),
        check=lambda payload: (payload.get("ok") is True, "Fixed hint."),
    )


def _answers(result: pi_bridge.ItemResult) -> list[dict[str, object]]:
    assert result.status == "submitted" and result.payload is not None
    return list(result.payload["answers"])


def test_session_scrubs_environment_and_removes_an_empty_private_home(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid")
    session, _ = _session(path)
    result = session.run_item(_item([]))
    assert result.status == "submitted"
    home = session._home
    session.close()
    assert session.home_was_empty is True
    assert home is not None and not Path(home).exists()


def test_per_turn_reservation_and_zero_cost_finalization(path: Path) -> None:
    session, run_id = _session(path)
    try:
        answers = _answers(
            session.run_item(
                _item(
                    [
                        {
                            "method": "ledger.reserve",
                            "params": {"item_id": "", "turn": 1},
                        },
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
                        {
                            "method": "ledger.reserve",
                            "params": {"item_id": "", "turn": 1},
                        },
                    ]
                )
            )
        )
    finally:
        session.close()
    assert answers[0]["ok"] is True and answers[1]["ok"] is True
    # The same (item, turn) is never dispatched twice.
    assert answers[2] == {
        "type": "rpc_result",
        "id": answers[2]["id"],
        "ok": False,
        "error": "budget_stopped",
    }
    row = (
        sqlite3.connect(path)
        .execute(
            "SELECT known_usd,pricing_version,usage_json,finalized_at "
            "FROM openai_attempts "
            "WHERE run_id=?",
            (run_id,),
        )
        .fetchone()
    )
    assert row[0] == "0" and row[1] == "custom-free" and row[3] is not None
    usage = json.loads(row[2])
    assert usage["input_tokens"] == 110 and usage["cached_tokens"] == 10
    assert usage["output_tokens"] == 20 and usage["reasoning_tokens"] == 5


def test_api_key_mode_prices_known_models_and_keeps_failures_unknown(
    path: Path,
) -> None:
    OpenAISettingsStore(path).save(
        OpenAIKey(api_key=SecretStr(secrets.token_urlsafe(24)))
    )
    config = AIConfig(model_id="gpt-5.6-luna")
    session, run_id = _session(path, config)

    def finalize(turn: int, **override: object) -> dict[str, object]:
        return {
            "method": "ledger.finalize",
            "params": {
                "attempt": "$last",
                "outcome": "completed",
                "http_status": 200,
                "model": "gpt-5.6-luna",
                "usage": USAGE,
                "cost_usd": 0.5,
            }
            | override,
        }

    def reserve(turn: int) -> dict[str, object]:
        return {"method": "ledger.reserve", "params": {"item_id": "", "turn": turn}}

    try:
        session.run_item(
            _item(
                [
                    reserve(1),
                    finalize(1),
                    reserve(2),
                    finalize(2, model="gpt-6-sol"),
                    reserve(3),
                    finalize(
                        3,
                        outcome="error",
                        http_status=429,
                        usage=dict.fromkeys(USAGE, 0),
                    ),
                    reserve(4),
                    finalize(4, outcome="aborted", http_status=None),
                ]
            )
        )
    finally:
        session.close()
    rows = (
        sqlite3.connect(path)
        .execute(
            "SELECT known_usd,pricing_version FROM openai_attempts WHERE run_id=? "
            "ORDER BY reserved_at",
            (run_id,),
        )
        .fetchall()
    )
    assert Decimal(rows[0][0]) > 0 and rows[0][1] == "2026-09-09"
    assert (rows[1][0], rows[1][1]) == ("0.5", "pi-ai-1.1.0-catalog")
    assert (rows[2][0], rows[2][1]) == ("0", "error-envelope-http-429")
    assert rows[3][0] is None  # transport loss: unknown, never assumed free


def test_credentials_resolve_only_for_the_active_provider_and_rotate_atomically(
    path: Path,
) -> None:
    store = AISettingsStore(path)
    initial = OAuthCredential(
        type="oauth",
        access="old-access",
        refresh="old-refresh",
        expires=int(time.time() * 1000) - 1,
        accountId="acct",
    )
    store.save_oauth("codex_device", initial)
    config = AIConfig(access_mode="codex_device", model_id="gpt-5.6-luna")
    session, _ = _session(path, config)
    rotated = {
        "type": "oauth",
        "access": "new-access",
        "refresh": "new-refresh",
        "expires": int(time.time() * 1000) + 3_600_000,
        "accountId": "acct",
    }
    try:
        answers = _answers(
            session.run_item(
                _item(
                    [
                        {
                            "method": "credential.read",
                            "params": {"provider_id": "openai"},
                        },
                        {
                            "method": "credential.read",
                            "params": {"provider_id": "openai-codex"},
                        },
                        {
                            "method": "credential.lock",
                            "params": {"provider_id": "openai-codex"},
                        },
                        {
                            "method": "credential.commit",
                            "params": {"lock": "$last", "credential": rotated},
                        },
                        {
                            "method": "credential.commit",
                            "params": {"lock": "$last", "credential": rotated},
                        },
                        {
                            "method": "credential.delete",
                            "params": {"provider_id": "openai-codex"},
                        },
                        {
                            "method": "credential.lock",
                            "params": {"provider_id": "openai-codex"},
                        },
                        {
                            "method": "credential.commit",
                            "params": {
                                "lock": "$last",
                                "credential": rotated | {"unexpected": "x"},
                            },
                        },
                    ]
                )
            )
        )
    finally:
        session.close()
    assert answers[0]["result"] == {"credential": None}
    assert answers[1]["result"]["credential"]["refresh"] == "old-refresh"  # type: ignore[index]
    assert answers[3]["ok"] is True
    assert answers[4] | {"id": 0} == {
        "type": "rpc_result",
        "id": 0,
        "ok": False,
        "error": "invalid_lock",
    }
    assert answers[5]["ok"] is False  # the sidecar may never delete
    assert answers[7]["ok"] is False and answers[7]["error"] == "invalid_params"
    # The rotated refresh token was persisted before acknowledgement, and the
    # malformed commit released its lock without overwriting it.
    assert store.load_slot("codex_device")["refresh"] == "new-refresh"  # type: ignore[index]
    assert all(lock.acquire(blocking=False) for lock in AISettingsStore._locks.values())
    for lock in AISettingsStore._locks.values():
        lock.release()


def test_custom_token_check_pay_and_submission_rpcs(path: Path) -> None:
    token = secrets.token_urlsafe(24)
    AISettingsStore(path).save_custom_token(CustomToken(token=SecretStr(token)))
    session, _ = _session(path)
    try:
        answers = _answers(
            session.run_item(
                _item(
                    [
                        {
                            "method": "credential.read",
                            "params": {"provider_id": "custom"},
                        },
                        {
                            "method": "credential.lock",
                            "params": {"provider_id": "custom"},
                        },
                        {
                            "method": "tool.check_pay",
                            "params": {"item_id": "", "text": "$25.50 CAD per hour"},
                        },
                        {
                            "method": "submission.check",
                            "params": {"item_id": "", "payload": {"ok": False}},
                        },
                        {
                            "method": "submission.check",
                            "params": {"item_id": "", "payload": {"ok": True}},
                        },
                        {
                            "method": "tool.check_pay",
                            "params": {"item_id": "x-unknown", "text": "1"},
                        },
                    ]
                )
            )
        )
    finally:
        session.close()
    assert answers[0]["result"] == {"credential": {"type": "api_key", "key": token}}
    assert answers[1]["ok"] is False  # non-OAuth credentials are never modified
    pay = answers[2]["result"]
    assert pay["state"] == "recognized"  # type: ignore[index]
    assert pay["value"]["minimum"] == "25.50"  # type: ignore[index]
    assert pay["value"]["currency"] == "CAD"  # type: ignore[index]
    assert answers[3]["result"] == {"accepted": False, "hint": "Fixed hint."}
    assert answers[4]["result"] == {"accepted": True}
    assert answers[5]["error"] == "unknown_item"


def test_malformed_sidecar_output_fails_closed_without_echo(path: Path) -> None:
    marker = secrets.token_urlsafe(16)
    session, _ = _session(path)
    try:
        result = session.run_item(
            _item(
                [],
                raw=json.dumps({"type": "done", "leak": marker}),
            )
        )
        assert result == pi_bridge.ItemResult("failed", None, "protocol", 0, 0)
        assert marker not in repr(result)
        again = session.run_item(_item([]))
        assert again.category == "protocol"
    finally:
        session.close()


def test_item_failure_categories_pass_through_and_start_validates_mode(
    path: Path,
) -> None:
    session, _ = _session(path)
    try:
        result = session.run_item(_item([], status="failed", category="max_turns"))
        assert (result.status, result.category, result.payload) == (
            "failed",
            "max_turns",
            None,
        )
    finally:
        session.close()
    with pytest.raises(SidecarError):
        PiSession(path, CUSTOM, str(uuid4()))  # custom mode needs a validated endpoint
    with pytest.raises(SidecarError):
        PiSession(path, AIConfig(), str(uuid4()))  # no model chosen


def _crash_after_reserve(path: Path, config: AIConfig) -> str:
    session, run_id = _session(path, config)
    try:
        result = session.run_item(
            _item(
                [{"method": "ledger.reserve", "params": {"item_id": "", "turn": 1}}],
                crash=True,
            )
        )
        assert result.status == "failed"
    finally:
        session.close()
    return run_id


def test_crashed_zero_cost_session_finalizes_abandoned_attempts_as_known_zero(
    path: Path,
) -> None:
    run_id = _crash_after_reserve(path, CUSTOM)
    rows = (
        sqlite3.connect(path)
        .execute(
            "SELECT known_usd,pricing_version,finalized_at FROM openai_attempts "
            "WHERE run_id=?",
            (run_id,),
        )
        .fetchall()
    )
    assert len(rows) == 1
    assert rows[0][0] == "0" and rows[0][1] == "custom-free-abandoned"
    assert rows[0][2] is not None
    AttemptLedger(path).require_resumable(run_id)  # no unknown usage remains


def test_crashed_paid_session_keeps_attempts_unknown_for_operator_review(
    path: Path,
) -> None:
    OpenAISettingsStore(path).save(
        OpenAIKey(api_key=SecretStr(secrets.token_urlsafe(24)))
    )
    run_id = _crash_after_reserve(path, AIConfig(model_id="gpt-5.6-luna"))
    row = (
        sqlite3.connect(path)
        .execute(
            "SELECT known_usd,finalized_at FROM openai_attempts WHERE run_id=?",
            (run_id,),
        )
        .fetchone()
    )
    assert row == (None, None)
