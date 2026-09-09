"""Generic markup controls, not fabricated job records."""

import pytest

from waterlooworks_flash_next.content import ContentError, _ContentParser


@pytest.mark.parametrize(
    "attributes",
    [
        "hidden",
        'hidden="false"',
        'aria-hidden="true"',
        'aria-hidden=" TRUE "',
        'style="display:none"',
        'style="DISPLAY: /* note */ none !important"',
        'style="visibility:hidden"',
        'style="visibility:collapse"',
        'style="content-visibility:hidden"',
    ],
)
def test_hidden_nested_text_and_enclosing_fields_are_excluded(attributes: str) -> None:
    parser = _ContentParser()
    parser.feed(
        "<div><span>Job Summary:</span><p>kept"
        f"<span {attributes}><b>withheld</b></span></p></div>"
    )
    assert parser.fields() == {"Job Summary:": "kept"}
    assert "withheld" not in repr(parser.root)
    parser = _ContentParser()
    parser.feed(f"<div {attributes}><span>Job Summary:</span><p>withheld</p></div>")
    assert parser.fields() == {}


def test_hidden_void_controls_do_not_suppress_following_text() -> None:
    parser = _ContentParser()
    parser.feed(
        '<div><span>Job Summary:</span><p><input hidden value="withheld">'
        '<span aria-hidden="false" style="visibility:visible">kept</span></p></div>'
    )
    assert parser.fields() == {"Job Summary:": "kept"}


@pytest.mark.parametrize(
    "style", ["display:var(--mode)", "visibility:inherit", r"displ\61y:none"]
)
def test_unsupported_inline_visibility_fails_closed(style: str) -> None:
    parser = _ContentParser()
    with pytest.raises(ContentError):
        parser.feed(f'<span style="{style}">withheld</span>')
