"""Responses input identity; historical Qwen framing remains read-compatible only."""

import hashlib
import json

from waterlooworks_flash_next.model_validation import (
    EXTRACTION_INSTRUCTIONS,
    INPUT_VERSION,
    VALIDATOR_VERSION,
    ExtractionCandidate,
    ModelOutputError,
    _canonical_input,
)
from waterlooworks_flash_next.openai_provider import TEMPLATE_VERSION, strict_schema
from waterlooworks_flash_next.openai_usage import MODEL
from waterlooworks_flash_next.parser_models import MAX_FIELD_LENGTH


def build_responses_prompt(
    fields: dict[str, str],
    *,
    request_compensation: bool,
    request_skills: bool,
    deterministic_skills: tuple[str, ...],
) -> tuple[str, str]:
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
    identity = json.dumps(
        {
            "provider": "openai-responses",
            "instructions": EXTRACTION_INSTRUCTIONS,
            "model": MODEL,
            "reasoning": "low",
            "service_tier": "default",
            "input_version": INPUT_VERSION,
            "template_version": TEMPLATE_VERSION,
            "validator_version": VALIDATOR_VERSION,
            "schema": strict_schema(ExtractionCandidate.model_json_schema()),
            "input": canonical,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return canonical.replace("<", r"\u003c").replace(">", r"\u003e"), hashlib.sha256(
        identity.encode()
    ).hexdigest()
