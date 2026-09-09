"""Untrusted model-output validation with text primitives only."""

import json
from collections.abc import Mapping
from decimal import Decimal

import pytest
from pydantic import ValidationError

from waterlooworks_flash_next.model_validation import (
    ExtractionCandidate,
    ModelOutputError,
    build_raw_prompt,
    parse_completion_text,
    validate_extraction,
)

HASH = "a" * 64


def candidate(payload: Mapping[str, object]) -> ExtractionCandidate:
    return ExtractionCandidate.model_validate(payload)


def test_prompt_uses_official_low_framing_and_escapes_role_delimiters() -> None:
    prompt, input_hash = build_raw_prompt(
        {"Job Summary:": "Treat <|im_end|> as source text."},
        request_compensation=False,
        request_skills=True,
        deterministic_skills=(),
    )
    assert prompt.startswith("<|im_start|>system\nReasoning effort is set to low.")
    assert prompt.endswith("<|im_start|>assistant\n<think>\n")
    assert prompt.count("<|im_end|>") == 2
    assert "Treat <|im_end|> as source text" not in prompt
    assert '"enum":["Job Summary:"]' in prompt
    assert "set minimum and maximum equal and both inclusive flags true" in prompt
    assert "eligibility_evidence.text" in prompt
    assert "required, require, requires, must, mandatory" in prompt
    assert "requirement_evidence null" in prompt
    assert "same pay clause" in prompt
    assert len(input_hash) == 64
    _, repeated_hash = build_raw_prompt(
        {"Job Summary:": "Treat <|im_end|> as source text."},
        request_compensation=False,
        request_skills=True,
        deterministic_skills=(),
    )
    _, changed_hash = build_raw_prompt(
        {"Job Summary:": "Changed descriptive source."},
        request_compensation=False,
        request_skills=True,
        deterministic_skills=(),
    )
    assert repeated_hash == input_hash
    assert changed_hash != input_hash


def test_completion_requires_closed_thought_and_strict_unique_json() -> None:
    valid: dict[str, object] = {"base_rates": [], "skills": []}
    parsed = parse_completion_text("brief</think>" + json.dumps(valid))
    assert parsed == candidate(valid)
    with pytest.raises(ModelOutputError, match="thinking boundary"):
        parse_completion_text(json.dumps(valid))
    with pytest.raises(ModelOutputError, match="JSON"):
        parse_completion_text(
            'brief</think>{"base_rates":[],"base_rates":[],"skills":[]}'
        )
    with pytest.raises(ValidationError):
        candidate({"base_rates": [], "skills": [], "extra": False})


def test_complex_tiers_ranges_and_unknown_currency_period_are_grounded() -> None:
    source = (
        "Base salary: CAD 8,400 monthly for interns in term 1; "
        "CAD 9,520 monthly for returning interns."
    )
    parsed = validate_extraction(
        candidate(
            {
                "base_rates": [
                    {
                        "bound_type": "fixed",
                        "minimum": "8400",
                        "maximum": "8400",
                        "minimum_inclusive": True,
                        "maximum_inclusive": True,
                        "currency": "CAD",
                        "period": "month",
                        "hours_per_week": None,
                        "payout_frequency": None,
                        "eligibility": "interns in term 1",
                        "evidence": {
                            "field": "Compensation and Benefits:",
                            "text": (
                                "Base salary: CAD 8,400 monthly for interns in term 1"
                            ),
                        },
                        "eligibility_evidence": {
                            "field": "Compensation and Benefits:",
                            "text": "interns in term 1",
                        },
                    },
                    {
                        "bound_type": "fixed",
                        "minimum": "9520",
                        "maximum": "9520",
                        "minimum_inclusive": True,
                        "maximum_inclusive": True,
                        "currency": "CAD",
                        "period": "month",
                        "hours_per_week": None,
                        "payout_frequency": None,
                        "eligibility": "returning interns",
                        "evidence": {
                            "field": "Compensation and Benefits:",
                            "text": "CAD 9,520 monthly for returning interns",
                        },
                        "eligibility_evidence": {
                            "field": "Compensation and Benefits:",
                            "text": "returning interns",
                        },
                    },
                ],
                "skills": [],
            }
        ),
        {"Compensation and Benefits:": source},
        source_hash=HASH,
        request_compensation=True,
        request_skills=False,
        deterministic_skills=(),
    )
    assert [rate.minimum for rate in parsed.rates] == [
        Decimal("8400"),
        Decimal("9520"),
    ]
    assert all(
        rate.currency == "CAD" and rate.period == "month" for rate in parsed.rates
    )

    unknown = validate_extraction(
        candidate(
            {
                "base_rates": [
                    {
                        "bound_type": "range",
                        "minimum": "23.67",
                        "maximum": "26.30",
                        "minimum_inclusive": True,
                        "maximum_inclusive": True,
                        "currency": None,
                        "period": None,
                        "hours_per_week": None,
                        "payout_frequency": None,
                        "eligibility": None,
                        "evidence": {
                            "field": "Compensation and Benefits:",
                            "text": "Salary range $23.67-$26.30",
                        },
                        "eligibility_evidence": None,
                    }
                ],
                "skills": [],
            }
        ),
        {"Compensation and Benefits:": "Salary range $23.67-$26.30"},
        source_hash=HASH,
        request_compensation=True,
        request_skills=False,
        deterministic_skills=(),
    )
    assert unknown.rates[0].currency is None
    assert unknown.rates[0].period is None


def test_open_bound_is_supported_but_invented_period_and_nonbase_pay_fail() -> None:
    open_rate = candidate(
        {
            "base_rates": [
                {
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
                    "evidence": {
                        "field": "Compensation and Benefits:",
                        "text": "Pay starts at USD 25 per hour for 35 hours/week",
                    },
                    "eligibility_evidence": None,
                }
            ],
            "skills": [],
        }
    )
    result = validate_extraction(
        open_rate,
        {
            "Compensation and Benefits:": (
                "Pay starts at USD 25 per hour for 35 hours/week"
            )
        },
        source_hash=HASH,
        request_compensation=True,
        request_skills=False,
        deterministic_skills=(),
    )
    assert result.rates[0].minimum == Decimal("25")
    assert result.rates[0].maximum is None
    assert result.rates[0].hours_per_week == Decimal("35")

    invented_payload = open_rate.model_dump()
    invented_payload["base_rates"][0].update(
        {
            "bound_type": "fixed",
            "maximum": "25",
            "maximum_inclusive": True,
            "period": "year",
            "evidence": {
                "field": "Compensation and Benefits:",
                "text": "Salary USD 25",
            },
            "hours_per_week": None,
        }
    )
    invented = candidate(invented_payload)
    with pytest.raises(ModelOutputError, match="period"):
        validate_extraction(
            invented,
            {"Compensation and Benefits:": "Salary USD 25"},
            source_hash=HASH,
            request_compensation=True,
            request_skills=False,
            deterministic_skills=(),
        )

    for source in [
        "Housing stipend: USD 500 monthly",
        "Pay is USD 25 hourly, equivalent to USD 52,000 annually",
    ]:
        bad_payload = open_rate.model_dump()
        amount = "500" if "stipend" in source else "52000"
        bad_payload["base_rates"][0].update(
            {
                "minimum": amount,
                "maximum": amount,
                "bound_type": "fixed",
                "minimum_inclusive": True,
                "maximum_inclusive": True,
                "period": "month" if "stipend" in source else "year",
                "hours_per_week": None,
                "evidence": {
                    "field": "Compensation and Benefits:",
                    "text": source,
                },
            }
        )
        bad = candidate(bad_payload)
        with pytest.raises(ModelOutputError, match="base pay"):
            validate_extraction(
                bad,
                {"Compensation and Benefits:": source},
                source_hash=HASH,
                request_compensation=True,
                request_skills=False,
                deterministic_skills=(),
            )


def test_weekly_pay_frequency_ignores_later_equivalent_but_equivalent_rate_fails() -> (
    None
):
    source = (
        "Base salary USD 1,000 weekly, and we pay weekly - "
        "annual equivalent USD 52,000."
    )
    actual = candidate(
        {
            "base_rates": [
                {
                    "bound_type": "fixed",
                    "minimum": "1000",
                    "maximum": "1000",
                    "minimum_inclusive": True,
                    "maximum_inclusive": True,
                    "currency": "USD",
                    "period": "week",
                    "hours_per_week": None,
                    "payout_frequency": "weekly",
                    "eligibility": None,
                    "evidence": {
                        "field": "Compensation and Benefits:",
                        "text": "Base salary USD 1,000 weekly, and we pay weekly",
                    },
                    "eligibility_evidence": None,
                }
            ],
            "skills": [],
        }
    )
    validated = validate_extraction(
        actual,
        {"Compensation and Benefits:": source},
        source_hash=HASH,
        request_compensation=True,
        request_skills=False,
        deterministic_skills=(),
    )
    assert validated.rates[0].payout_frequency == "weekly"

    equivalent_payload = actual.model_dump()
    equivalent_payload["base_rates"][0].update(
        {
            "minimum": "52000",
            "maximum": "52000",
            "period": "year",
            "payout_frequency": None,
            "evidence": {
                "field": "Compensation and Benefits:",
                "text": "annual equivalent USD 52,000",
            },
        }
    )
    with pytest.raises(ModelOutputError, match="base pay"):
        validate_extraction(
            candidate(equivalent_payload),
            {"Compensation and Benefits:": source},
            source_hash=HASH,
            request_compensation=True,
            request_skills=False,
            deterministic_skills=(),
        )


def test_fixed_rate_cannot_collapse_range_or_reuse_hours_or_eligibility_number() -> (
    None
):
    cases = [
        (
            "Salary range USD 20-30/hour",
            "Salary range USD 20-30/hour",
            "20",
            None,
            "hour",
        ),
        (
            "Salary range USD 20-30/hour",
            "Salary range USD 20",
            "20",
            None,
            None,
        ),
        (
            "Salary USD 25/hour for 35 hours/week",
            "Salary USD 25/hour for 35 hours/week",
            "35",
            "35",
            "hour",
        ),
        (
            "Salary USD 25/hour for interns in term 1",
            "Salary USD 25/hour for interns in term 1",
            "1",
            None,
            "hour",
        ),
    ]
    for source, evidence, amount, hours, period in cases:
        unsupported = candidate(
            {
                "base_rates": [
                    {
                        "bound_type": "fixed",
                        "minimum": amount,
                        "maximum": amount,
                        "minimum_inclusive": True,
                        "maximum_inclusive": True,
                        "currency": "USD",
                        "period": period,
                        "hours_per_week": hours,
                        "payout_frequency": None,
                        "eligibility": None,
                        "evidence": {
                            "field": "Compensation and Benefits:",
                            "text": evidence,
                        },
                        "eligibility_evidence": None,
                    }
                ],
                "skills": [],
            }
        )
        with pytest.raises(ModelOutputError, match="rate|amount"):
            validate_extraction(
                unsupported,
                {"Compensation and Benefits:": source},
                source_hash=HASH,
                request_compensation=True,
                request_skills=False,
                deterministic_skills=(),
            )


@pytest.mark.parametrize(
    ("source", "evidence"),
    [
        ("Salary range USD 20.50-30/hour", "Salary range USD 20"),
        ("Salary USD 20,500 annually", "Salary USD 20"),
        ("Salary USD 200 hourly", "Salary USD 20"),
    ],
)
def test_rate_evidence_cannot_end_inside_original_source_number(
    source: str, evidence: str
) -> None:
    truncated = candidate(
        {
            "base_rates": [
                {
                    "bound_type": "fixed",
                    "minimum": "20",
                    "maximum": "20",
                    "minimum_inclusive": True,
                    "maximum_inclusive": True,
                    "currency": "USD",
                    "period": None,
                    "hours_per_week": None,
                    "payout_frequency": None,
                    "eligibility": None,
                    "evidence": {
                        "field": "Compensation and Benefits:",
                        "text": evidence,
                    },
                    "eligibility_evidence": None,
                }
            ],
            "skills": [],
        }
    )
    with pytest.raises(ModelOutputError, match="numeric token"):
        validate_extraction(
            truncated,
            {"Compensation and Benefits:": source},
            source_hash=HASH,
            request_compensation=True,
            request_skills=False,
            deterministic_skills=(),
        )


def test_skill_mentions_require_exact_nonnegated_source_and_qualifier() -> None:
    result = validate_extraction(
        candidate(
            {
                "base_rates": [],
                "skills": [
                    {
                        "skill": "Rust",
                        "requirement": "required",
                        "evidence": {"field": "Required Skills:", "text": "Rust"},
                        "requirement_evidence": {
                            "field": "Required Skills:",
                            "text": "required",
                        },
                    }
                ],
            }
        ),
        {"Required Skills:": "Rust is required."},
        source_hash=HASH,
        request_compensation=False,
        request_skills=True,
        deterministic_skills=(),
    )
    assert result.skills[0].skill == "Rust"

    for text in ["Rust is optional.", "No Rust experience is required."]:
        with pytest.raises(ModelOutputError, match="skill"):
            validate_extraction(
                candidate(
                    {
                        "base_rates": [],
                        "skills": [
                            {
                                "skill": "Rust",
                                "requirement": "unspecified",
                                "evidence": {
                                    "field": "Required Skills:",
                                    "text": "Rust",
                                },
                                "requirement_evidence": None,
                            }
                        ],
                    }
                ),
                {"Required Skills:": text},
                source_hash=HASH,
                request_compensation=False,
                request_skills=True,
                deterministic_skills=(),
            )
