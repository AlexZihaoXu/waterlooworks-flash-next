"""Primitive skill fragments; no postings or provider-response fixtures."""

import pytest

from waterlooworks_flash_next.model_validation import (
    ExtractionCandidate,
    ModelOutputError,
    ValidatedSkill,
    validate_extraction,
)
from waterlooworks_flash_next.parser_models import Requirement


def extract(
    source: str,
    skill: str,
    requirement: Requirement = "unspecified",
    qualifier: str | None = None,
    quote: str | None = None,
) -> ValidatedSkill:
    field = "Required Skills:"
    candidate = ExtractionCandidate.model_validate(
        {
            "base_rates": [],
            "skills": [
                {
                    "skill": skill,
                    "requirement": requirement,
                    "evidence": {"field": field, "text": quote or source},
                    "requirement_evidence": (
                        {"field": field, "text": qualifier} if qualifier else None
                    ),
                }
            ],
        }
    )
    return validate_extraction(
        candidate,
        {field: source},
        source_hash="a" * 64,
        request_compensation=False,
        request_skills=True,
        deterministic_skills=(),
    ).skills[0]


def test_skill_is_narrowed_inside_larger_supporting_quote() -> None:
    source = "Experience with Rust tooling."
    result = extract(source, "rust")
    assert result.evidence.text == "Rust"
    assert result.evidence.matches(source)


@pytest.mark.parametrize(
    "marker,requirement", [("required", "required"), ("preferred", "preferred")]
)
def test_qualifier_is_narrowed_inside_larger_supporting_quote(
    marker: str, requirement: Requirement
) -> None:
    source = f"Rust is {marker}."
    result = extract(source, "Rust", requirement, source)
    assert result.requirement_evidence is not None
    assert result.requirement_evidence.text == marker
    assert result.requirement_evidence.matches(source)


@pytest.mark.parametrize(
    "source,skill",
    [("JavaScript tooling", "Java"), ("C++ tooling", "C"), ("Rust tooling", "Ruby")],
)
def test_absent_names_and_partial_tokens_still_reject(source: str, skill: str) -> None:
    with pytest.raises(ModelOutputError):
        extract(source, skill)


def test_qualifier_in_another_clause_does_not_qualify_skill() -> None:
    source = "Rust tooling. Go is required."
    with pytest.raises(ModelOutputError, match="requirement"):
        extract(source, "Rust", "required", source)


def test_broad_quote_cannot_hide_negation() -> None:
    with pytest.raises(ModelOutputError, match="negated"):
        extract("No Rust experience needed. Go is required.", "Rust")


@pytest.mark.parametrize(
    "source,skill", [("JavaScript", "Java"), ("C++", "C"), ("SuperRust", "Rust")]
)
def test_truncated_quotes_cannot_create_whole_skill_tokens(
    source: str, skill: str
) -> None:
    with pytest.raises(ModelOutputError):
        extract(source, skill, quote=skill)


@pytest.mark.parametrize("source", ["Rust requirements", "Rust requires tooling"])
def test_truncated_qualifier_quotes_do_not_create_requirement_tokens(
    source: str,
) -> None:
    with pytest.raises(ModelOutputError, match="requirement"):
        extract(source, "Rust", "required", "require")


def test_conflicting_qualifiers_are_not_selected_arbitrarily() -> None:
    source = "Rust is preferred or required."
    with pytest.raises(ModelOutputError, match="requirement"):
        extract(source, "Rust", "required", source)
