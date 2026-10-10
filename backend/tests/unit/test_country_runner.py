"""Country validation port and Pi configuration identity; pure functions only."""

import json

import pytest

from waterlooworks_flash_next.ai_settings import AIConfig
from waterlooworks_flash_next.country_inference import _configuration_valid
from waterlooworks_flash_next.country_runner import (
    country_input,
    country_schema,
    run_configuration,
    validate_country,
)

FIELDS = {
    "listing:city": "Toronto",
    "content:Job - Country:": "Canada",
    "content:Job Summary:": "Our head office is in Germany.",
}


def _answer(**override: object) -> dict[str, object]:
    return {
        "country": "CA",
        "confidence": 0.95,
        "reason": "location_context",
        "evidence": [{"field": "content:Job - Country:", "quote": "Canada"}],
    } | override


def test_accepted_prediction_records_exact_code_point_offsets() -> None:
    result = validate_country(_answer(), FIELDS)
    assert result["status"] == "accepted" and result["country"] == "CA"
    assert result["evidence"][0]["start"] == 0 and result["evidence"][0]["end"] == 6
    assert set(result["evidence"][0]) == {"field", "start", "end", "quote_hash"}


@pytest.mark.parametrize(
    "answer",
    [
        _answer(extra=1),
        _answer(country="XX"),
        _answer(confidence=True),
        _answer(confidence=1.5),
        _answer(reason="low_confidence"),
        _answer(evidence=[]),
        _answer(evidence=[{"field": "content:Job - Country:", "quote": "Mars"}]),
        _answer(evidence=[{"field": "missing", "quote": "Canada"}]),
        _answer(evidence=[{"field": "content:Job Summary:", "quote": "head office"}]),
        _answer(country=None, confidence=0.9, evidence=[], reason="low_confidence"),
        _answer(country=None, confidence=None, evidence=[]),
    ],
)
def test_invalid_answers_are_rejected(answer: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        validate_country(answer, FIELDS)


def test_ambiguity_and_confidence_floor_become_unknown() -> None:
    assert validate_country(_answer(confidence=0.8), FIELDS)["reason"] == (
        "low_confidence"
    )
    remote = {"listing:city": "Remote - worldwide", "content:Job - Country:": "Canada"}
    assert validate_country(_answer(), remote)["reason"] == "ambiguous_location"
    london = {"listing:city": "London"}
    answer = _answer(evidence=[{"field": "listing:city", "quote": "London"}])
    assert validate_country(answer, london)["reason"] == "ambiguous_location"
    unknown = validate_country(
        _answer(
            country=None, confidence=None, evidence=[], reason="no_location_context"
        ),
        FIELDS,
    )
    assert unknown == {
        "status": "unknown",
        "country": None,
        "confidence": None,
        "evidence": [],
        "reason": "no_location_context",
    }


def test_inputs_are_location_only_and_bounded() -> None:
    fields, reason = country_input(
        {"city": "Waterloo", "title": "ignored"},
        {"Job - Country:": "Canada", "Compensation and Benefits:": "ignored"},
    )
    assert fields == {"listing:city": "Waterloo", "content:Job - Country:": "Canada"}
    assert reason is None
    assert country_input({}, {}) == ({}, "no_location_context")
    assert country_input({}, {"Job Summary:": "x" * 30000})[1] == "input_too_large"
    schema = country_schema(fields)
    item = schema["properties"]["evidence"]["items"]
    assert item["properties"]["field"]["enum"] == list(fields)
    assert schema["additionalProperties"] is False


def test_pi_country_configuration_is_valid_on_read_and_tamper_evident() -> None:
    raw, digest = run_configuration(AIConfig(model_id="gpt-5.6-luna"))
    assert _configuration_valid(json.loads(raw), raw, digest)
    tampered = json.loads(raw) | {"prompt_hash": "0" * 64}
    tampered_raw = json.dumps(tampered, separators=(",", ":"))
    assert not _configuration_valid(tampered, tampered_raw, digest)
    other = run_configuration(
        AIConfig(model_id="gpt-5.6-luna", reasoning_effort="high")
    )
    assert other[1] != digest


class _Session:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome

    def run_item(self, _item: object) -> object:
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def test_sidecar_errors_become_halting_infrastructure_failures() -> None:
    from waterlooworks_flash_next.country_runner import infer_one
    from waterlooworks_flash_next.pi_bridge import ItemResult, SidecarError

    paid = AIConfig(model_id="gpt-5.6-luna")
    for raised in (SidecarError("connection"), RuntimeError("boom")):
        result, halt = infer_one(_Session(raised), "1", FIELDS, paid)  # type: ignore[arg-type]
        assert result == {"status": "failed", "reason": "infrastructure"}
        assert halt is True
    result, halt = infer_one(
        _Session(ItemResult("failed", None, "max_turns", 6, 3)),  # type: ignore[arg-type]
        "1",
        FIELDS,
        paid,
    )
    assert (result["reason"], halt) == ("invalid_output", False)
    answer = _answer()
    result, halt = infer_one(
        _Session(ItemResult("submitted", answer, None, 2, 1)),  # type: ignore[arg-type]
        "1",
        FIELDS,
        paid,
    )
    assert (result["status"], halt) == ("accepted", False)
