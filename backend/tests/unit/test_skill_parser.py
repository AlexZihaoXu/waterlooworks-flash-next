"""Explicit technology words in field primitives, not posting fixtures."""

from waterlooworks_flash_next.skill_parser import parse_skills


def test_exact_tokens_aliases_and_evidence() -> None:
    text = "C++, JavaScript, Java, Kubernetes, CI-CD and object-oriented design."
    result = parse_skills({"Required Skills:": text})
    assert {match.skill for match in result.matches} == {
        "C++",
        "JavaScript",
        "Java",
        "Kubernetes",
        "CI/CD",
        "Object-oriented design",
    }
    for match in result.matches:
        assert match.evidence.matches(text)
        assert match.requirement == "unspecified"
    assert (
        not parse_skills({"Required Skills:": "JavaScript"}).matches[0].skill == "Java"
    )


def test_negation_requirement_scope_and_field_allowlist() -> None:
    result = parse_skills(
        {
            "Required Skills:": (
                "Python required; Java preferred; Kubernetes not required."
            ),
            "Job Title:": "C++",
            "Targeted Degrees and Disciplines:": "SQL",
        }
    )
    assert [(x.skill, x.requirement) for x in result.matches] == [
        ("Python", "required"),
        ("Java", "preferred"),
    ]
    assert result.excluded[0].skill == "Kubernetes"
    assert result.excluded[0].reason == "negated_or_optional_context"
    assert (
        parse_skills({"Job Summary:": "No experience with Java needed."}).matches == ()
    )


def test_unknown_concepts_and_embedded_tokens_are_not_inferred() -> None:
    result = parse_skills(
        {"Job Responsibilities:": "Teamwork and architecture; JavaBeans and C++++."}
    )
    assert result.matches == ()
    assert result.state == "unknown"
    assert result.source_fields["Job Responsibilities:"]


def test_explicit_requirement_has_its_own_evidence_span() -> None:
    text = "Java is required. Python is preferred."
    result = parse_skills({"Required Skills:": text})
    for mention in result.matches:
        assert mention.requirement_evidence is not None
        assert mention.requirement_evidence.matches(text)
        assert mention.requirement_evidence.text == mention.requirement
