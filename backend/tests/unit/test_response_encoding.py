"""Codec-level tests only; no job postings, credentials, or HTTP calls."""

import pytest

from waterlooworks_flash_next.listings import (
    FailureCategory,
    ListingFetchError,
    decode_response_text,
)


def test_prefers_valid_utf8_over_legacy_header() -> None:
    text = '"\u00e9"'
    assert decode_response_text(text.encode("utf-8"), "iso-8859-1") == text


def test_accepts_utf8_bom() -> None:
    assert decode_response_text(b"\xef\xbb\xbf{}", None) == "{}"


def test_decodes_declared_latin1_when_utf8_is_invalid() -> None:
    assert decode_response_text(b'"\xe9"', "ISO-8859-1") == '"\u00e9"'


def test_missing_charset_does_not_guess_or_replace_bytes() -> None:
    with pytest.raises(ListingFetchError) as caught:
        decode_response_text(b'"\xe9"', None)
    assert caught.value.category is FailureCategory.ENCODING
    assert not caught.value.retryable


def test_unsupported_charset_fails_without_echoing_it() -> None:
    with pytest.raises(ListingFetchError) as caught:
        decode_response_text(b'"\xe9"', "unsupported")
    assert "unsupported" not in str(caught.value)
