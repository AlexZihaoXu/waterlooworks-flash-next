"""Pi prompt construction: compact header, inline budget and cache identity."""

import json

from waterlooworks_flash_next.ai_settings import AIConfig
from waterlooworks_flash_next.inference_prompt import (
    INLINE_BUDGET_CHARS,
    PI_EXTRACTION_INSTRUCTIONS,
    build_pi_prompt,
    strict_schema,
)
from waterlooworks_flash_next.model_validation import ExtractionCandidate

CONFIG = AIConfig(model_id="gpt-5.6-luna", reasoning_effort="low")


def _build(fields: dict[str, str], config: AIConfig = CONFIG):  # type: ignore[no-untyped-def]
    return build_pi_prompt(
        fields,
        request_compensation=True,
        request_skills=True,
        deterministic_skills=("Python",),
        config=config,
    )


def test_small_postings_are_inline_and_escape_role_markup() -> None:
    prompt, _ = _build(
        {"Job Summary:": "<b>Build</b> things", "Required Skills:": "Go"}
    )
    header = json.loads(prompt.user)
    assert header["request"] == {"compensation": True, "skills": True}
    assert header["deterministic_skills"] == ["Python"]
    assert header["inline_text"]["Job Summary:"] == "<b>Build</b> things"
    assert "<b>" not in prompt.user  # escaped as \\u003c in the raw message
    assert "submit_extraction" in prompt.system
    assert "output only JSON" not in PI_EXTRACTION_INSTRUCTIONS


def test_large_postings_send_an_index_and_prioritise_requested_sections() -> None:
    fields = {
        "Compensation and Benefits:": "USD 25 per hour",
        "Required Skills:": "Python",
        "Zeta Notes:": "x" * (INLINE_BUDGET_CHARS + 10),
    }
    prompt, _ = _build(fields)
    header = json.loads(prompt.user)
    inline = header["inline_text"]
    assert "Compensation and Benefits:" in inline and "Required Skills:" in inline
    assert "Zeta Notes:" not in inline
    sizes = {entry["name"]: entry for entry in header["sections"]}
    assert sizes["Zeta Notes:"] == {
        "name": "Zeta Notes:",
        "chars": INLINE_BUDGET_CHARS + 10,
        "inline": False,
    }
    # The header stays compact; the full text is only reachable through tools.
    assert len(prompt.user) < 1000
    assert dict(prompt.sections)["Zeta Notes:"] == fields["Zeta Notes:"]


def test_identity_changes_with_configuration_and_schema_is_strict() -> None:
    fields = {"Job Summary:": "Build things"}
    _, base = _build(fields)
    for change in (
        {"model_id": "gpt-5.6-sol"},
        {"reasoning_effort": "high"},
        {"max_turns": 3},
        {"access_mode": "chatgpt_signin"},
    ):
        assert _build(fields, CONFIG.model_copy(update=change))[1] != base
    assert _build(fields, CONFIG.model_copy(update={"parallel_streams": 4}))[1] == base
    prompt, _ = _build(fields)
    schema = prompt.submit_schema
    assert "$defs" not in json.dumps(schema) and "$ref" not in json.dumps(schema)
    evidence = schema["properties"]["skills"]["items"]["properties"]["evidence"]
    assert evidence["properties"]["field"]["enum"] == ["Job Summary:"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"base_rates", "skills"}
    assert strict_schema(ExtractionCandidate.model_json_schema())["required"]


def test_header_says_when_the_full_posting_is_inline() -> None:
    small, _ = _build({"Job Summary:": "Build things"})
    assert json.loads(small.user)["complete"] is True
    assert "submit_extraction now" in json.loads(small.user)["next"]
    large, _ = _build({"Zeta Notes:": "x" * (INLINE_BUDGET_CHARS + 1)})
    assert json.loads(large.user)["complete"] is False
    assert "FIRST reply" in small.system and "<amount>" in small.system


def test_submission_schema_accepts_text_or_offsets_for_every_evidence() -> None:
    prompt, _ = _build({"Job Summary:": "Build things"})
    rate = prompt.submit_schema["properties"]["base_rates"]["items"]
    for evidence in (
        rate["properties"]["evidence"],
        rate["properties"]["eligibility_evidence"]["anyOf"][0],
    ):
        assert set(evidence["required"]) == {"field", "text", "start", "end"}
        assert evidence["additionalProperties"] is False
