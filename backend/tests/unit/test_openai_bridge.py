"""Bridge framing/errors and configuration primitives; no provider response fixtures."""

import hashlib
import json
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

from waterlooworks_flash_next.inference_reuse import CONTRACT, compatible_origin
from waterlooworks_flash_next.openai_bridge import BridgeMessage


def test_bridge_invalid_json_and_extra_fields_are_sanitized() -> None:
    secret = secrets.token_urlsafe()
    for payload in (secret, json.dumps({"id": 0, "op": "initialize", "extra": secret})):
        result = subprocess.run(
            [sys.executable, "-m", "waterlooworks_flash_next.openai_bridge"],
            input=payload + "\n",
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert secret not in result.stdout + result.stderr
        assert json.loads(result.stdout)["error"] == "bridge_unavailable"
        assert not result.stderr


def test_bridge_contract_defaults_never_load_environment() -> None:
    value = BridgeMessage(id=0, op="initialize")
    assert value.load_environment is False
    assert value.max_output_tokens == 4096


@pytest.mark.parametrize("kind", ["country", "extraction"])
def test_origin_configuration_compatibility_is_allowlisted(kind: str) -> None:
    contract = CONTRACT[kind]
    config = {
        "version": contract["version"],
        "provider": "openai-responses",
        "providerVersion": CONTRACT["openaiProfile"],
        "reusePolicy": CONTRACT["policy"],
        "model": {"id": CONTRACT["openaiModel"]},
        "reasoning": "low",
        "serviceTier": "default",
        "codeHashes": {contract["validatorFile"]: contract["validatorHash"]},
    }
    if kind == "country":
        config.update(
            prompt_hash=contract["promptHash"], countries_hash=contract["countriesHash"]
        )
    else:
        config.update(
            profile="v7",
            structuredOutput=True,
            maxTokens=4096,
            promptHashes={
                "salary": contract["salaryPromptHash"],
                "keywords": contract["keywordsPromptHash"],
            },
        )
    raw = json.dumps(config)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    assert compatible_origin(raw, digest, raw, digest, kind)
    assert not compatible_origin(raw, "0" * 64, raw, digest, kind)
    assert not compatible_origin(
        "{}", hashlib.sha256(b"{}").hexdigest(), raw, digest, kind
    )
    config["version"] = "obsolete"
    changed = json.dumps(config)
    assert not compatible_origin(
        changed, hashlib.sha256(changed.encode()).hexdigest(), raw, digest, kind
    )


def test_bridge_failed_admission_releases_temporary_database_lock(
    tmp_path: Path,
) -> None:
    from waterlooworks_flash_next.database import open_database
    from waterlooworks_flash_next.provider_lease import ProviderLease

    path = tmp_path / "empty.sqlite3"
    open_database(path).close()
    with ProviderLease(path):
        payload = {"id": 0, "op": "initialize", "database": str(path)}
        result = subprocess.run(
            [sys.executable, "-m", "waterlooworks_flash_next.openai_bridge"],
            input=json.dumps(payload) + "\n",
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert json.loads(result.stdout)["error"] == "bridge_unavailable"
        assert not result.stderr
    with ProviderLease(path) as lease:
        lease.require_owner(path)
