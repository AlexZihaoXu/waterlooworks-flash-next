"""Pi agent prompts, tool schemas and configuration-aware input identity.

The configuration identifier ("v9-pi-tools" plus a hash of access mode,
endpoint, model, reasoning effort and caps) is part of every input hash and of
the cache key, so results from different setups never collide. Historical
Responses/Qwen rows remain readable through their own identities.
"""

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from waterlooworks_flash_next.ai_settings import AIConfig
from waterlooworks_flash_next.model_validation import (
    EXTRACTION_INSTRUCTIONS,
    INPUT_VERSION,
    VALIDATOR_VERSION,
    ExtractionCandidate,
    ModelOutputError,
    _canonical_input,
)
from waterlooworks_flash_next.parser_models import MAX_FIELD_LENGTH
from waterlooworks_flash_next.skill_parser import SKILL_FIELDS

PI_TEMPLATE_VERSION = "v9-pi-tools"
# Historical OpenAI Responses identity, kept only to read earlier results.
LEGACY_RESPONSES_TEMPLATE = "openai-responses-low-strict-v1"
LEGACY_RESPONSES_MODEL = "gpt-5.6-luna"
INLINE_BUDGET_CHARS = 12_000
_JSON_SUFFIX = (
    "No tools or external actions. After one brief evidence check, output only JSON."
)
TOOL_INSTRUCTIONS = (
    "Saved sections are given inline or through list_sections, read_section and "
    "find_span; inline text is identical to the section text. check_pay can "
    "normalise one pay sentence. Finish by calling submit_extraction exactly "
    "once with the final result; never answer in prose."
)
if _JSON_SUFFIX not in EXTRACTION_INSTRUCTIONS:  # pragma: no cover - import guard
    raise RuntimeError("Extraction instructions changed; update the Pi template.")
PI_EXTRACTION_INSTRUCTIONS = EXTRACTION_INSTRUCTIONS.replace(
    _JSON_SUFFIX, TOOL_INSTRUCTIONS
)


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Strict JSON schema: every key required, no extras, $refs inlined."""
    definitions = schema.get("$defs", {})

    def convert(value: Any) -> Any:
        if isinstance(value, list):
            return [convert(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            name = value["$ref"].rsplit("/", 1)[-1]
            return convert(copy.deepcopy(definitions[name]))
        result = {
            key: convert(item)
            for key, item in value.items()
            if key not in ("default", "title", "$defs")
        }
        if result.get("type") == "object":
            result["additionalProperties"] = False
            result["required"] = list(result.get("properties", {}))
        return result

    return dict(convert(schema))


def config_identity(config: AIConfig) -> dict[str, Any]:
    return {
        "template": PI_TEMPLATE_VERSION,
        "mode": config.access_mode,
        "provider": config.provider_id,
        "endpoint": config.endpoint,
        "model": config.model_id,
        "reasoning": config.reasoning_effort,
        "max_turns": config.max_turns,
        "max_tool_calls": config.max_tool_calls,
        "max_output_tokens": config.max_output_tokens,
        "reasoning_field": config.custom_reasoning_field,
        "disable_with": config.custom_disable_with,
    }


def config_hash(config: AIConfig) -> str:
    raw = json.dumps(config_identity(config), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def job_template_version(config: AIConfig) -> str:
    return f"{PI_TEMPLATE_VERSION}+{config_hash(config)}"


def job_model_id(config: AIConfig) -> str:
    return f"pi:{config.access_mode}:{config.model_id}"


def _escape(text: str) -> str:
    return text.replace("<", r"\u003c").replace(">", r"\u003e")


@dataclass(frozen=True)
class PiPrompt:
    system: str
    user: str
    sections: tuple[tuple[str, str], ...]
    submit_schema: dict[str, Any]


def extraction_schema(fields: dict[str, str]) -> dict[str, Any]:
    schema = ExtractionCandidate.model_json_schema()
    schema["$defs"]["EvidenceCandidate"]["properties"]["field"] = {
        "type": "string",
        "enum": sorted(fields),
    }
    return strict_schema(schema)


def _inline(fields: dict[str, str], priority: tuple[str, ...]) -> dict[str, str]:
    order = [name for name in priority if name in fields] + sorted(
        name for name in fields if name not in priority
    )
    if sum(len(value) for value in fields.values()) <= INLINE_BUDGET_CHARS:
        return {name: fields[name] for name in order}
    chosen: dict[str, str] = {}
    used = 0
    for name in order:
        if used + len(fields[name]) <= INLINE_BUDGET_CHARS:
            chosen[name] = fields[name]
            used += len(fields[name])
    return chosen


def build_pi_prompt(
    fields: dict[str, str],
    *,
    request_compensation: bool,
    request_skills: bool,
    deterministic_skills: tuple[str, ...],
    config: AIConfig,
) -> tuple[PiPrompt, str]:
    """Compact header + section index; small postings are fully inline."""
    if not fields or any(
        not isinstance(value, str) or not value or len(value) > MAX_FIELD_LENGTH
        for value in fields.values()
    ):
        raise ModelOutputError("Model input fields violate the saved-content contract.")
    canonical = _canonical_input(
        fields,
        request_compensation=request_compensation,
        request_skills=request_skills,
        deterministic_skills=deterministic_skills,
    )
    priority = (("Compensation and Benefits:",) if request_compensation else ()) + (
        SKILL_FIELDS if request_skills else ()
    )
    inline = _inline(fields, priority)
    header = {
        "request": {"compensation": request_compensation, "skills": request_skills},
        "deterministic_skills": list(deterministic_skills),
        "sections": [
            {"name": name, "chars": len(value), "inline": name in inline}
            for name, value in sorted(fields.items())
        ],
        "inline_text": inline,
    }
    user = _escape(
        json.dumps(header, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    )
    schema = extraction_schema(fields)
    identity = json.dumps(
        {
            "provider": "pi",
            "configuration": config_identity(config),
            "instructions": PI_EXTRACTION_INSTRUCTIONS,
            "input_version": INPUT_VERSION,
            "validator_version": VALIDATOR_VERSION,
            "inline_budget": INLINE_BUDGET_CHARS,
            "schema": schema,
            "input": canonical,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    prompt = PiPrompt(
        system=PI_EXTRACTION_INSTRUCTIONS,
        user=user,
        sections=tuple(sorted(fields.items())),
        submit_schema=schema,
    )
    return prompt, hashlib.sha256(identity.encode()).hexdigest()
