"""Grounded pay fragments, not fabricated postings/provider responses."""

import pytest

from waterlooworks_flash_next.model_validation import (
    ExtractionCandidate,
    ModelOutputError,
    ValidatedExtraction,
    validate_extraction,
)

FIELD = "Compensation and Benefits:"
HASH = "a" * 64


def extract(source: str, *, eligibility: str | None = None) -> ValidatedExtraction:
    return validate_extraction(
        ExtractionCandidate.model_validate(
            {
                "base_rates": [
                    {
                        "bound_type": "fixed",
                        "minimum": "25",
                        "maximum": "25",
                        "minimum_inclusive": True,
                        "maximum_inclusive": True,
                        "currency": "USD",
                        "period": "week",
                        "hours_per_week": None,
                        "payout_frequency": None,
                        "eligibility": eligibility,
                        "evidence": {"field": FIELD, "text": source},
                        "eligibility_evidence": (
                            {"field": FIELD, "text": source} if eligibility else None
                        ),
                    }
                ],
                "skills": [],
            }
        ),
        {FIELD: source},
        source_hash=HASH,
        request_compensation=True,
        request_skills=False,
        deterministic_skills=(),
    )


def test_currency_after_explicit_pay_period_is_grounded() -> None:
    assert extract("Salary: $25/week USD").rates[0].currency == "USD"


def test_conflicting_currency_after_pay_period_is_not_silently_selected() -> None:
    with pytest.raises(ModelOutputError, match="currency"):
        extract("Salary: CAD $25/week USD")


def test_exact_tier_label_can_have_a_larger_supporting_quote() -> None:
    source = "Term 1: USD $25/week"
    rate = extract(source, eligibility="Term 1").rates[0]
    assert rate.eligibility_evidence is not None
    assert rate.eligibility_evidence.text == "Term 1"
    assert rate.eligibility_evidence.matches(source)


@pytest.mark.parametrize("label", ["Term 2", "Term 1", "first term"])
def test_unstated_or_partial_tier_labels_are_rejected(label: str) -> None:
    with pytest.raises(ModelOutputError, match="eligibility"):
        extract("Term 12: USD $25/week", eligibility=label)
