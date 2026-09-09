"""Empty/invalid contracts and primitive markup; never posting fixtures."""

import pytest
from pydantic import ValidationError

from waterlooworks_flash_next.content import (
    ContentError,
    StoredPostingContent,
    _ContentParser,
    extract_content,
)


def test_content_only_requires_reviewed_fields_without_inventing_metadata() -> None:
    with pytest.raises(ValidationError):
        StoredPostingContent.model_validate({})
    with pytest.raises(ValidationError):
        StoredPostingContent.model_validate(
            {"id": "", "coverage": "metadata_only", "fields": {}}
        )
    assert "metadata" not in StoredPostingContent.model_fields
    assert StoredPostingContent.model_json_schema()["additionalProperties"] is False


def test_parser_discards_executable_text_attributes_and_comments() -> None:
    parser = _ContentParser()
    parser.feed(
        '<div data-token="private"><script>private</script>'
        "<!--private--><span>text</span></div>"
    )
    assert parser.fields() == {}
    assert "private" not in repr(parser.root)
    assert "text" in repr(parser.root)
    with pytest.raises(ContentError, match="diagnostics suppressed"):
        extract_content("<p>text</p>", "", "", None)
