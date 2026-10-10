"""Evidence-by-reference resolution and fixed reason codes (synthetic fields)."""

import pytest

from waterlooworks_flash_next.pi_submission import (
    SubmissionContext,
    SubmissionRejected,
    normalize_with_map,
    resolve_payload,
    validate_submission,
)

PAY = "Pay starts at USD 25 per hour for 35 hours/week"
FIELDS = {
    "Compensation and Benefits:": PAY,
    "Required Skills:": "Python is required. Go – preferred.",
}
HASH = "a" * 64


def _context(compensation: bool = True, skills: bool = False) -> SubmissionContext:
    return SubmissionContext(FIELDS, HASH, compensation, skills, ())


def _rate(evidence: dict[str, object], **override: object) -> dict[str, object]:
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
    } | override


def _ref(field: str, start: int | None, end: int | None, text: str | None = None):  # type: ignore[no-untyped-def]
    return {"field": field, "start": start, "end": end, "text": text}


def test_offsets_reconstruct_exact_text_and_pass_validation() -> None:
    payload = {
        "base_rates": [_rate(_ref("Compensation and Benefits:", 0, len(PAY)))],
        "skills": [],
    }
    result = validate_submission(payload, _context())
    assert str(result.rates[0].minimum) == "25"
    assert result.rates[0].evidence.text == PAY


def test_legacy_exact_text_and_normalized_text_map_to_the_source_span() -> None:
    legacy: dict[str, object] = {"field": "Compensation and Benefits:", "text": PAY}
    resolved = resolve_payload({"base_rates": [_rate(legacy)], "skills": []}, FIELDS)
    assert resolved["base_rates"][0]["evidence"]["text"] == PAY
    sloppy = _ref("Required Skills:", None, None, "  go - PREFERRED  ")
    resolved = resolve_payload(
        {
            "base_rates": [],
            "skills": [
                {
                    "skill": "Go",
                    "requirement": "preferred",
                    "evidence": sloppy,
                    "requirement_evidence": None,
                }
            ],
        },
        FIELDS,
    )
    text = resolved["skills"][0]["evidence"]["text"]
    assert text == "Go – preferred"
    assert text in FIELDS["Required Skills:"]


def test_normalization_maps_every_character_back_to_the_source() -> None:
    normalized, index = normalize_with_map("A  “B”—C")
    assert normalized == 'a "b"-c'
    assert len(index) == len(normalized)
    assert index[0] == 0 and index[-1] == 7


@pytest.mark.parametrize(
    ("evidence", "code"),
    [
        (
            _ref("Compensation and Benefits:", None, None, "USD 99 per hour"),
            "evidence_not_found",
        ),
        (_ref("Compensation and Benefits:", 5, 999), "evidence_offsets_invalid"),
        (_ref("Compensation and Benefits:", None, None, None), "evidence_missing"),
        (_ref("Not a section:", 0, 3), "evidence_field_unsupported"),
    ],
)
def test_resolution_failures_have_fixed_codes(
    evidence: dict[str, object], code: str
) -> None:
    with pytest.raises(SubmissionRejected) as error:
        validate_submission({"base_rates": [_rate(evidence)], "skills": []}, _context())
    assert error.value.code == code
    assert error.value.path == "base_rates[0].evidence"
    assert "USD 99" not in error.value.hint and "Pay starts" not in error.value.hint


def test_validator_failures_name_the_path_and_section_but_no_values() -> None:
    whole = _ref("Compensation and Benefits:", 0, len(PAY))
    cases = [
        (_rate(whole, currency="EUR"), "rate_currency_unsupported"),
        (_rate(whole, period="year"), "rate_period_unsupported"),
        (_rate(whole, minimum="30"), "rate_amount_unsupported"),
    ]
    for rate, code in cases:
        with pytest.raises(SubmissionRejected) as error:
            validate_submission({"base_rates": [rate], "skills": []}, _context())
        assert error.value.code == code
        assert error.value.path == "base_rates[0]"
        assert error.value.section == "Compensation and Benefits:"
        hint = error.value.hint
        assert hint.startswith(f"Rejected [{code}]: base_rates[0]")
        assert "EUR" not in hint and "30" not in hint and "Pay starts" not in hint


def test_unrequested_category_and_schema_paths() -> None:
    whole = _ref("Compensation and Benefits:", 0, len(PAY))
    with pytest.raises(SubmissionRejected) as error:
        validate_submission(
            {"base_rates": [_rate(whole)], "skills": []}, _context(compensation=False)
        )
    assert (error.value.code, error.value.path) == (
        "unrequested_category",
        "base_rates",
    )
    with pytest.raises(SubmissionRejected) as error:
        validate_submission(
            {"base_rates": [_rate(whole, currency="usd")], "skills": []}, _context()
        )
    assert error.value.code == "schema"
    assert error.value.path == "base_rates[0].currency"
    with pytest.raises(SubmissionRejected) as error:
        validate_submission({"base_rates": []}, _context())
    assert error.value.code == "schema"
