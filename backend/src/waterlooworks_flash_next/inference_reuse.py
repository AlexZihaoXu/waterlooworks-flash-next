"""Allowlisted origin compatibility; never substitute a different producer identity."""

import hashlib
import json
from pathlib import Path
from typing import Any

CONTRACT = json.loads(
    Path(__file__)
    .with_name("inference-reuse-contract.json")
    .read_text(encoding="utf-8")
)


def configuration_valid(raw: str, digest: str, kind: str) -> bool:
    try:
        if hashlib.sha256(raw.encode()).hexdigest() != digest:
            return False
        config = json.loads(raw)
        contract = CONTRACT[kind]
        if (
            config.get("version") != contract["version"]
            or config.get("codeHashes", {}).get(contract["validatorFile"])
            != contract["validatorHash"]
        ):
            return False
        model = config["model"]
        if config.get("provider") == "openai-responses":
            if (
                model.get("id") != CONTRACT["openaiModel"]
                or config.get("reasoning") != "low"
                or config.get("serviceTier") != "default"
                or config.get("providerVersion") != CONTRACT["openaiProfile"]
            ):
                return False
        elif (
            config.get("provider") is not None
            or model.get("id") != CONTRACT["legacyModel"]
            or config.get("reasoning") != "none"
            or model.get("quantization", {}).get("name") != "Q2_K_XL"
            or model.get("config", {}).get("context_length") != 65536
            or model.get("config", {}).get("parallel") != 4
        ):
            return False
        if kind == "extraction":
            return bool(
                config.get("structuredOutput") is True
                and config.get("maxTokens") == 4096
                and config.get("profile") in ("v7", "v8-shared-job-prefix")
                and config.get("promptHashes", {}).get("salary")
                == contract["salaryPromptHash"]
                and config.get("promptHashes", {}).get("keywords")
                == contract["keywordsPromptHash"]
                and (
                    config.get("profile") != "v8-shared-job-prefix"
                    or config.get("prefixVersion") == "shared-job-details-v1"
                )
            )
        return bool(
            config.get("prompt_hash") == contract["promptHash"]
            and config.get("countries_hash") == contract["countriesHash"]
        )
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def compatible_origin(
    origin_raw: str, origin_hash: str, current_raw: str, current_hash: str, kind: str
) -> bool:
    if not configuration_valid(
        origin_raw, origin_hash, kind
    ) or not configuration_valid(current_raw, current_hash, kind):
        return False
    current: dict[str, Any] = json.loads(current_raw)
    return origin_hash == current_hash or (
        current.get("provider") == "openai-responses"
        and current.get("reusePolicy") == CONTRACT["policy"]
    )
