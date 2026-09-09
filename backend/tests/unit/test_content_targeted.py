"""Targeted-field markup primitives, not fabricated postings."""

import pytest

from waterlooworks_flash_next.content import _ContentParser

LABEL = "Targeted Degrees and Disciplines:"
CONTROLS = (
    "<div><strong>Targeted Clusters</strong></div>"
    "<button>View Targeted Degrees and Disciplines</button>"
)


def targeted_fields(body: str) -> dict[str, str]:
    parser = _ContentParser()
    parser.feed(f"<div><span>{LABEL}</span><p>{body}</p></div>")
    return parser.fields()


def test_targeted_controls_without_list_are_not_data() -> None:
    assert targeted_fields(CONTROLS) == {}
    assert targeted_fields("plain text without a recorded list") == {}


def test_targeted_list_preserves_values_without_controls() -> None:
    assert targeted_fields(
        CONTROLS + "<ul><li>first value<button>control</button></li>"
        "<li>second value</li></ul>"
    ) == {LABEL: "first value\nsecond value"}


def test_absent_targeted_field_stays_absent() -> None:
    parser = _ContentParser()
    parser.feed("<ul><li>outside value</li></ul>")
    assert parser.fields() == {}


@pytest.mark.parametrize(
    "attributes", ["hidden", 'style="display:none"', 'aria-hidden="true"']
)
def test_targeted_hidden_lists_remain_excluded(attributes: str) -> None:
    assert targeted_fields(CONTROLS + f"<ul {attributes}><li>withheld</li></ul>") == {}


def test_targeted_list_cannot_include_navigation_or_executable_text() -> None:
    assert targeted_fields(
        "<nav><ul><li>withheld</li></ul></nav>"
        "<ul><li>kept<script>withheld</script><span hidden>withheld</span></li></ul>"
    ) == {LABEL: "kept"}


def collapsed_widget(attributes: str = 'style="display:none"') -> str:
    return f"<div>{CONTROLS}<ul {attributes}><li>first value</li></ul></div>"


@pytest.mark.parametrize(
    "style",
    ["display:none", " DISPLAY : NONE !important ;", "display:/* note */none"],
)
def test_observed_collapsed_widget_preserves_list_text(style: str) -> None:
    assert targeted_fields(collapsed_widget(f'style="{style}"')) == {
        LABEL: "first value"
    }


@pytest.mark.parametrize(
    "attributes",
    [
        'hidden style="display:none"',
        'aria-hidden="true" style="display:none"',
        'style="display:none;visibility:hidden"',
        'style="display:none;content-visibility:hidden"',
    ],
)
def test_collapsed_widget_does_not_override_other_hiding(attributes: str) -> None:
    assert targeted_fields(collapsed_widget(attributes)) == {}


@pytest.mark.parametrize(
    "body",
    [
        collapsed_widget().replace("View Targeted Degrees and Disciplines", "Other"),
        collapsed_widget().replace(CONTROLS, ""),
        collapsed_widget().replace("<div>", "<div hidden>", 1),
        collapsed_widget().replace("<button>", '<button aria-hidden="true">'),
        collapsed_widget().replace("<strong>", "<strong hidden>"),
        collapsed_widget().replace("<ul ", "<ol ").replace("</ul>", "</ol>"),
        CONTROLS + '<div><ul style="display:none"><li>withheld</li></ul></div>',
    ],
)
def test_collapsed_widget_requires_visible_observed_association(body: str) -> None:
    assert targeted_fields(body) == {}


@pytest.mark.parametrize("label", ["Job Summary:", "Unknown:"])
def test_collapsed_widget_is_not_recovered_for_other_labels(label: str) -> None:
    parser = _ContentParser()
    parser.feed(f"<div><span>{label}</span><p>{collapsed_widget()}</p></div>")
    assert "first value" not in "".join(parser.fields().values())


def test_collapsed_widget_outside_field_is_suppressed() -> None:
    parser = _ContentParser()
    parser.feed(collapsed_widget())
    assert "first value" not in repr(parser.root)


@pytest.mark.parametrize("tag", ["div", "p"])
def test_collapsed_widget_cannot_bypass_hidden_field_ancestors(tag: str) -> None:
    parser = _ContentParser()
    markup = f"<div><span>{LABEL}</span><p>{collapsed_widget()}</p></div>"
    parser.feed(markup.replace(f"<{tag}>", f"<{tag} hidden>", 1))
    assert parser.fields() == {}


def test_collapsed_and_expanded_widget_preserve_identical_source_text() -> None:
    value = "- Theme - Computing: Software Development"
    collapsed = collapsed_widget().replace("first value", value)
    expanded = collapsed.replace('style="display:none"', 'style="display:block"')
    assert targeted_fields(collapsed) == targeted_fields(expanded) == {LABEL: value}


def test_collapsed_widget_retains_only_unhidden_item_text() -> None:
    body = collapsed_widget().replace(
        "first value",
        "first value<script>withheld</script><span hidden>withheld</span>"
        '<span aria-hidden="true">withheld</span>'
        '<span style="display:none">withheld</span>'
        "<nav>withheld</nav><button>withheld</button>"
        '<input value="withheld"><select><option>withheld</option></select>',
    )
    assert targeted_fields(body) == {LABEL: "first value"}


@pytest.mark.parametrize(
    "style", ["display:none;display:var(--mode)", "display:none;visibility:inherit"]
)
def test_collapsed_widget_rejects_unsupported_visibility(style: str) -> None:
    from waterlooworks_flash_next.content import ContentError

    with pytest.raises(ContentError):
        targeted_fields(collapsed_widget(f'style="{style}"'))
