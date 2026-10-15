"""Sanitized description formatting from generic markup controls.

Structural markup only (placeholder words, reserved example.com hosts); never
posting fixtures, network access or raw HTML storage.
"""

import json
from typing import Any, cast

import pytest
from pydantic import ValidationError

from waterlooworks_flash_next import rich_text
from waterlooworks_flash_next.content import (
    StoredPostingFormatting,
    extract_content,
    extract_content_with_formatting,
)
from waterlooworks_flash_next.content_storage import EXTRACTOR_VERSION
from waterlooworks_flash_next.database import canonical_snapshot
from waterlooworks_flash_next.rich_text import (
    FORMAT_VERSION,
    MAX_LIST_DEPTH,
    Paragraph,
    safe_href,
)

REQUIRED = (
    "<div><span>Job Title:</span><p>Title</p></div>"
    "<div><span>Organization:</span><p>Organization</p></div>"
    "<div><span>Work Term:</span><p>Term</p></div>"
)
# Captured with the pre-formatting extractor: the plain text and its content
# hash must stay byte-identical so saved analysis keeps matching.
MARKUP = (
    "<div><div><span>Job Title:</span><p>Title</p></div>"
    "<div><span>Organization:</span><p>Organization</p></div>"
    "<div><span>Work Term:</span><p>Term</p></div>"
    "<div><span>Job Summary:</span><p>Intro <strong>bold</strong> and "
    "<em>italic</em> text.<br>Second line with "
    '<a href="https://example.com/path?q=1&amp;r=2">a link</a>.<br><br>'
    "New paragraph.</p></div>"
    "<div><span>Job Responsibilities:</span><p><ul><li>First item</li>"
    "<li>Second <b>item</b><ul><li>Nested</li></ul></li></ul>"
    "<ol><li>Step one</li><li>Step <i>two</i></li></ol></p></div>"
    '<div><span>Required Skills:</span><p><a href="javascript:alert(1)">unsafe</a> '
    '<a href="mailto:person@example.com">mail</a> <a href="/relative">rel</a>'
    "</p></div></div>"
)
GOLDEN_FIELDS = {
    "Job Title:": "Title",
    "Organization:": "Organization",
    "Work Term:": "Term",
    "Job Summary:": (
        "Intro bold and italic text.\nSecond line with a link.\nNew paragraph."
    ),
    "Job Responsibilities:": "First item\nSecond itemNested\nStep one\nStep two",
    "Required Skills:": "unsafe mail rel",
}
GOLDEN_HASH = "76759085ba4373ed936851e62082a0444c2fb50ebe1bf147fc34cd5100c70641"


def _text(value: str, **marks: bool) -> dict[str, object]:
    return {"type": "text", "text": value, **marks}


def _paragraph(*content: dict[str, object]) -> dict[str, object]:
    return {"type": "paragraph", "content": list(content)}


def _summary(markup: str) -> str:
    return REQUIRED + f"<div><span>Job Summary:</span><p>{markup}</p></div>"


def _formatting(markup: str, **kwargs: tuple[str, ...]) -> StoredPostingFormatting:
    return extract_content_with_formatting(
        _summary(markup), "100001", "Title", "Organization", **kwargs
    )[1]


def _dump(formatting: StoredPostingFormatting) -> dict[str, Any]:
    return cast(
        dict[str, Any], json.loads(formatting.model_dump_json(exclude_defaults=True))
    )


def test_plain_text_and_content_hash_stay_byte_identical() -> None:
    content, formatting = extract_content_with_formatting(
        MARKUP, "100001", "Title", "Organization"
    )
    assert content.fields == GOLDEN_FIELDS
    assert canonical_snapshot(content.model_dump())[1] == GOLDEN_HASH
    assert extract_content(MARKUP, "100001", "Title", "Organization") == content
    # The extractor version that marks saved text current is unchanged.
    assert EXTRACTOR_VERSION == "labels-text-v3"
    assert formatting.version == FORMAT_VERSION
    assert "formatting" not in content.model_dump()


def test_whitelisted_structure_is_kept_and_everything_else_stripped() -> None:
    _, formatting = extract_content_with_formatting(
        MARKUP, "100001", "Title", "Organization"
    )
    fields = _dump(formatting)["fields"]
    assert isinstance(fields, dict)
    # Single unmarked paragraphs add nothing over the plain text.
    assert set(fields) == {
        "Job Summary:",
        "Job Responsibilities:",
        "Required Skills:",
    }
    assert fields["Job Summary:"] == [
        _paragraph(
            _text("Intro "),
            _text("bold", bold=True),
            _text(" and "),
            _text("italic", italic=True),
            _text(" text."),
            {"type": "break"},
            _text("Second line with "),
            {
                "type": "link",
                "href": "https://example.com/path?q=1&r=2",
                "text": "a link",
            },
            _text("."),
        ),
        _paragraph(_text("New paragraph.")),
    ]
    assert fields["Job Responsibilities:"] == [
        {
            "type": "list",
            "ordered": False,
            "items": [
                [_paragraph(_text("First item"))],
                [
                    _paragraph(_text("Second "), _text("item", bold=True)),
                    {
                        "type": "list",
                        "ordered": False,
                        "items": [[_paragraph(_text("Nested"))]],
                    },
                ],
            ],
        },
        {
            "type": "list",
            "ordered": True,
            "items": [
                [_paragraph(_text("Step one"))],
                [_paragraph(_text("Step "), _text("two", italic=True))],
            ],
        },
    ]
    # javascript: and relative links lose their target; mailto stays.
    assert fields["Required Skills:"] == [
        _paragraph(
            _text("unsafe "),
            {"type": "link", "href": "mailto:person@example.com", "text": "mail"},
            _text(" rel"),
        )
    ]
    serialized = formatting.model_dump_json()
    for withheld in ("<", ">", "javascript", "/relative", "script"):
        assert withheld not in serialized


@pytest.mark.parametrize(
    "href",
    [
        "https://example.com",
        "http://example.com/a?b=c#d",
        "HTTPS://Example.com/path",
        "mailto:person@example.com",
    ],
)
def test_safe_links_are_allowed(href: str) -> None:
    assert safe_href(href) == href


@pytest.mark.parametrize(
    "href",
    [
        None,
        "",
        "javascript:alert(1)",
        " javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "data:text/html;base64,AAAA",
        "vbscript:msgbox",
        "file:///etc/passwd",
        "ftp://example.com/",
        "/relative/path",
        "relative",
        "//example.com/protocol-relative",
        "https://",
        "https:example.com",
        "https://user:secret@example.com/",
        "https://example.com/\npath",
        "https://example.com/ path",
        "https://example.com/\x00",
        "https:\\\\example.com",
        "mailto:person@example.com?subject=x",
        "mailto:a@b@example.com",
        "mailto:",
        "https://example.com/" + "a" * 2100,
    ],
)
def test_unsafe_links_are_dropped(href: str | None) -> None:
    assert safe_href(href) is None


def test_hidden_and_blocked_markup_never_enters_formatting() -> None:
    formatting = _formatting(
        "<strong>kept</strong> words"
        '<span hidden><a href="https://example.com/hidden">withheld</a></span>'
        "<script>withheld()</script><style>.x{}</style>"
        '<input type="hidden" value="withheld"><textarea>withheld</textarea>'
    )
    serialized = formatting.model_dump_json()
    assert "withheld" not in serialized and "hidden" not in serialized
    assert _dump(formatting)["fields"] == {
        "Job Summary:": [_paragraph(_text("kept", bold=True), _text(" words"))]
    }


def test_session_bearing_links_lose_their_target_but_keep_text() -> None:
    secret = "opaque-session-value"
    formatting = _formatting(
        f'<b>Apply</b> <a href="https://example.com/?s={secret}">here</a>',
        secrets=(secret,),
    )
    assert secret not in formatting.model_dump_json()
    assert _dump(formatting)["fields"] == {
        "Job Summary:": [_paragraph(_text("Apply", bold=True), _text(" here"))]
    }


def test_trivial_and_targeted_fields_store_no_formatting() -> None:
    assert _dump(_formatting("Only plain words."))["fields"] == {}
    markup = REQUIRED + (
        "<div><span>Job Summary:</span><p><b>x</b></p></div>"
        "<div><span>Targeted Degrees and Disciplines:</span>"
        "<p><ul><li><b>One</b></li><li>Two</li></ul></p></div>"
    )
    _, formatting = extract_content_with_formatting(
        markup, "100001", "Title", "Organization"
    )
    assert set(_dump(formatting)["fields"]) == {"Job Summary:"}


def test_excessive_nesting_falls_back_to_plain_text_only() -> None:
    deep = (
        "<ul><li>" * (MAX_LIST_DEPTH + 1) + "deep" + "</li></ul>" * (MAX_LIST_DEPTH + 1)
    )
    content, formatting = extract_content_with_formatting(
        _summary("<b>a</b>" + deep), "100001", "Title", "Organization"
    )
    assert content.fields["Job Summary:"] == "adeep"
    assert _dump(formatting)["fields"] == {}


def test_whitespace_collapses_and_breaks_trim() -> None:
    formatting = _formatting(
        "<br>  <i>lead</i>   spaced \n\t words <br> <br><br><br>after<br><br>"
    )
    assert _dump(formatting)["fields"] == {
        "Job Summary:": [
            _paragraph(_text("lead", italic=True), _text(" spaced words")),
            _paragraph(_text("after")),
        ]
    }


def test_headings_and_paragraph_containers_become_paragraphs() -> None:
    formatting = _formatting("<h3>Heading</h3><div>Body <em>text</em></div><p>More</p>")
    assert _dump(formatting)["fields"] == {
        "Job Summary:": [
            _paragraph(_text("Heading", bold=True)),
            _paragraph(_text("Body "), _text("text", italic=True)),
            _paragraph(_text("More")),
        ]
    }


def test_stored_formatting_contract_rejects_unsafe_or_unknown_input() -> None:
    valid = {
        "version": FORMAT_VERSION,
        "fields": {"Job Summary:": [_paragraph(_text("x", bold=True))]},
    }
    assert StoredPostingFormatting.model_validate(valid).fields
    for invalid in (
        {**valid, "version": "other"},
        {**valid, "extra": 1},
        {**valid, "fields": {"Unreviewed:": valid["fields"]["Job Summary:"]}},  # type: ignore[index]
        {**valid, "fields": {"Job Summary:": []}},
        {**valid, "fields": {"Job Summary:": [_paragraph()]}},
        {**valid, "fields": {"Job Summary:": [_paragraph(_text(""))]}},
        {
            **valid,
            "fields": {
                "Job Summary:": [
                    _paragraph(
                        {"type": "link", "href": "javascript:alert(1)", "text": "x"}
                    )
                ]
            },
        },
        {
            **valid,
            "fields": {"Job Summary:": [{"type": "html", "html": "<b>x</b>"}]},
        },
        {
            **valid,
            "fields": {
                "Job Summary:": [{"type": "list", "ordered": False, "items": [[]]}]
            },
        },
    ):
        with pytest.raises(ValidationError):
            StoredPostingFormatting.model_validate(invalid)
    assert Paragraph.model_json_schema()["additionalProperties"] is False


def test_formatting_failure_never_withholds_plain_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("builder failure")

    monkeypatch.setattr(rich_text, "build_blocks", broken)
    content, formatting = extract_content_with_formatting(
        _summary("<b>bold</b> words"), "100001", "Title", "Organization"
    )
    assert content.fields["Job Summary:"] == "bold words"
    assert formatting.version == FORMAT_VERSION and formatting.fields == {}


def test_unbalanced_markup_still_formats_within_the_field() -> None:
    formatting = _formatting('<a href="https://example.com">open <b>bold</p>')
    assert _dump(formatting)["fields"] == {
        "Job Summary:": [
            _paragraph(
                {"type": "link", "href": "https://example.com", "text": "open "},
                {
                    "type": "link",
                    "href": "https://example.com",
                    "text": "bold",
                    "bold": True,
                },
            )
        ]
    }
