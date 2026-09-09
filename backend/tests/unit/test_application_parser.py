"""Generic instruction and reserved-domain URL primitives only."""

from waterlooworks_flash_next.application_parser import parse_application_instructions


def test_dual_application_overrides_method_only_reading_and_preserves_query() -> None:
    text = (
        "You must apply through WaterlooWorks AND directly to "
        "the employer at "
        "https://example.test/apply?role=1&source=board."
    )
    parsed = parse_application_instructions(
        {
            "Application Method:": "WaterlooWorks",
            "Additional Application Information:": text,
        }
    )
    assert parsed.external_application_required is True
    assert parsed.application_route == "waterlooworks_and_employer"
    assert (
        parsed.external_application_links[0].url
        == "https://example.test/apply?role=1&source=board"
    )
    assert parsed.external_application_links[0].evidence.matches(text)
    assert parsed.external_application_links[0].classification == "required"


def test_must_also_apply_optional_negation_and_missing() -> None:
    assert (
        parse_application_instructions(
            {
                ("Additional Application Information:"): (
                    "You must also apply at https://example.test/apply"
                )
            }
        ).external_application_required
        is True
    )
    optional = parse_application_instructions(
        {
            ("Additional Application Information:"): (
                "You may optionally apply externally at https://example.test/apply"
            )
        }
    )
    assert optional.external_application_required is False
    assert optional.external_application_links[0].classification == "optional"
    assert (
        parse_application_instructions(
            {
                ("Additional Application Information:"): (
                    "No external application is required."
                )
            }
        ).external_application_required
        is False
    )
    assert (
        parse_application_instructions(
            {"Application Method:": "WaterlooWorks"}
        ).external_application_required
        is None
    )


def test_info_links_unsafe_links_ambiguity_and_conflicts() -> None:
    result = parse_application_instructions(
        {
            ("Additional Application Information:"): (
                "Information session: "
                "https://example.test/session\nCompany website: "
                "https://example.test/\nApply: javascript:alert(1)"
            )
        }
    )
    assert result.external_application_required is None
    assert all(
        link.classification == "informational"
        for link in result.external_application_links
    )
    assert len(result.external_application_links) == 2
    conflict = parse_application_instructions(
        {
            ("Additional Application Information:"): (
                "External application is mandatory. No external "
                "application is required."
            )
        }
    )
    assert conflict.external_application_required is None
    assert conflict.state == "needs_analysis"


def test_required_company_website_instruction_classifies_link_as_required() -> None:
    text = "You must apply on the company website at https://example.test/apply"
    parsed = parse_application_instructions(
        {"Additional Application Information:": text}
    )
    assert parsed.external_application_required is True
    assert parsed.external_application_links[0].classification == "required"


def test_url_queries_and_informational_company_links() -> None:
    url = "https://example.test/apply?source=board;lang=en&role=1"
    parsed = parse_application_instructions(
        {"Additional Application Information:": "You must also apply at " + url}
    )
    assert parsed.external_application_links[0].url == url
    info = parse_application_instructions(
        {
            "Additional Application Information:": (
                "You must apply through WaterlooWorks. "
                "Company website: https://example.test/"
            )
        }
    )
    assert info.external_application_required is None
    assert info.external_application_links[0].classification == "informational"


def test_conditional_and_same_clause_conflicting_instructions_remain_unknown() -> None:
    for text in [
        "If invited, you must also apply externally.",
        "No external application is required unless requested.",
        "External application is optional but you must also apply externally.",
    ]:
        assert (
            parse_application_instructions(
                {"Additional Application Information:": text}
            ).external_application_required
            is None
        )


def test_imperative_dual_route_optional_route_and_unsafe_urls() -> None:
    dual = parse_application_instructions(
        {
            "Additional Application Information:": (
                "Apply through WaterlooWorks and directly to the employer."
            )
        }
    )
    assert dual.external_application_required is True
    assert dual.application_route == "waterlooworks_and_employer"
    optional = parse_application_instructions(
        {
            "Additional Application Information:": "You may also apply at https://example.test/apply"
        }
    )
    assert optional.external_application_required is False
    unsafe = parse_application_instructions(
        {
            "Additional Application Information:": (
                "Apply: https://user@example.test/ https://example.test:99999/ "
                "file:///local javascript:alert(1)"
            )
        }
    )
    assert unsafe.external_application_links == ()
