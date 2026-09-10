"""Provider contract checks use empty databases and structural primitives only."""

import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from waterlooworks_flash_next.database import open_database
from waterlooworks_flash_next.openai_ledger import (
    AttemptLedger,
    BudgetLimits,
    BudgetStopped,
)
from waterlooworks_flash_next.openai_provider import (
    ProviderError,
    build_request,
    response_text,
    strict_schema,
    usage_from_response,
)
from waterlooworks_flash_next.openai_usage import UsageCounters


def test_fixed_responses_envelope() -> None:
    body = build_request([], "extraction", {"type": "object", "properties": {}}, 4096)
    assert body["model"] == "gpt-5.6-luna"
    assert body["reasoning"] == {"effort": "low"}
    assert body["service_tier"] == "default"
    assert body["store"] is False
    assert body["max_output_tokens"] == 4096
    assert "temperature" not in body
    assert body["text"]["format"]["strict"] is True
    assert "response_format" not in body


def test_schema_requires_nullable_properties_without_defaults() -> None:
    schema = strict_schema(
        {
            "type": "object",
            "properties": {
                "value": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "default": None,
                }
            },
        }
    )
    assert schema["required"] == ["value"]
    assert schema["additionalProperties"] is False
    assert "default" not in json.dumps(schema)


def test_empty_response_is_not_success_or_free() -> None:
    assert usage_from_response({}) == UsageCounters()
    with pytest.raises(ProviderError, match="response_schema"):
        response_text({})


def test_usage_partition_reads_responses_names() -> None:
    usage = usage_from_response(
        {
            "usage": {
                "input_tokens": 12,
                "output_tokens": 5,
                "input_tokens_details": {"cached_tokens": 3, "cache_write_tokens": 2},
                "output_tokens_details": {"reasoning_tokens": 4},
            }
        }
    )
    assert usage.ordinary_input_tokens == 7
    assert usage.total_tokens == 17


def test_ledger_reserved_before_dispatch_and_unknown_blocks_resume(
    tmp_path: Path,
) -> None:
    path = tmp_path / "empty.sqlite3"
    open_database(path).close()
    ledger = AttemptLedger(path)
    run = str(uuid4())
    ledger.create_run(run, BudgetLimits(request_limit=2))
    attempt = ledger.reserve(run, "0" * 64)
    assert ledger.summary(run).attempts == 1
    with pytest.raises(BudgetStopped):
        ledger.require_resumable(run)
    ledger.finalize(attempt, UsageCounters(), model=None, service_tier=None)
    assert ledger.summary(run).total_usd is None
    with pytest.raises(BudgetStopped):
        ledger.reserve(run, "1" * 64)


def test_attempt_finalization_is_idempotent_and_budget_is_durable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "empty.sqlite3"
    open_database(path).close()
    ledger = AttemptLedger(path)
    run = str(uuid4())
    ledger.create_run(run, BudgetLimits(request_limit=1))
    attempt = ledger.reserve(run, "0" * 64)
    usage = UsageCounters(
        input_tokens=10,
        cached_tokens=0,
        cache_write_tokens=0,
        output_tokens=1,
        reasoning_tokens=1,
    )
    for _ in range(2):
        ledger.finalize(attempt, usage, model="gpt-5.6-luna", service_tier="default")
    assert ledger.summary(run).known_usd == Decimal("0.0000032")
    ledger.create_run(run, BudgetLimits(request_limit=100))
    with pytest.raises(BudgetStopped):
        AttemptLedger(path).reserve(run, "1" * 64)


def test_budget_limits_fail_closed() -> None:
    for value in ("NaN", "Infinity", "0", "101"):
        with pytest.raises(ValueError):
            BudgetLimits(known_cost_stop_usd=Decimal(value))


def test_provider_transport_loss_is_durable_and_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import secrets

    from cryptography.fernet import Fernet
    from pydantic import SecretStr

    from waterlooworks_flash_next import openai_provider
    from waterlooworks_flash_next.openai_settings import OpenAIKey, OpenAISettingsStore

    path = tmp_path / "empty.sqlite3"
    open_database(path).close()
    monkeypatch.setenv("WW_CREDENTIALS_KEY", Fernet.generate_key().decode())
    OpenAISettingsStore(path).save(
        OpenAIKey(api_key=SecretStr(secrets.token_urlsafe()))
    )
    provider = openai_provider.ResponsesProvider(path)
    run = str(uuid4())
    provider.ledger.create_run(run, BudgetLimits())
    calls = 0

    async def unavailable(*_args: object) -> tuple[int, dict[str, object]]:
        nonlocal calls
        calls += 1
        assert provider.ledger.summary(run).attempts == 1
        raise ProviderError("timeout")

    monkeypatch.setattr(openai_provider, "_dispatch", unavailable)
    with pytest.raises(ProviderError, match="^timeout$"):
        provider.complete(
            run, "0" * 64, [], "empty", {"type": "object", "properties": {}}
        )
    assert calls == 1
    assert provider.ledger.summary(run).unpriced_attempts == 1
    with pytest.raises(BudgetStopped):
        provider.complete(
            run, "1" * 64, [], "empty", {"type": "object", "properties": {}}
        )
    assert calls == 1


def test_admin_usage_is_empty_and_read_only(tmp_path: Path) -> None:
    from auth_support import session_client

    path = tmp_path / "empty.sqlite3"
    with session_client(path) as client:
        response = client.get("/api/admin/openai-usage")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["runs"] == []
        assert response.json()["default_request_limit"] == 20
        assert "not a hard monetary cap" in response.json()["warning"]
    connection = open_database(path)
    assert connection.execute("SELECT count(*) FROM openai_attempts").fetchone()[0] == 0
    connection.close()


def test_missing_provider_key_fails_without_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from waterlooworks_flash_next.credentials import CredentialError
    from waterlooworks_flash_next.openai_provider import ResponsesProvider

    monkeypatch.delenv("WW_CREDENTIALS_KEY", raising=False)
    path = tmp_path / "empty.sqlite3"
    open_database(path).close()
    provider = ResponsesProvider(path)
    with pytest.raises(CredentialError):
        provider.require_ready()
    connection = open_database(path)
    assert connection.execute("SELECT count(*) FROM openai_attempts").fetchone()[0] == 0
    connection.close()
