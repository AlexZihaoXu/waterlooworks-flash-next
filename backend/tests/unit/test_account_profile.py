"""Profile field primitives only; no Firebase/provider records or network access."""

import pytest
from pydantic import ValidationError

from waterlooworks_flash_next.account_profile import AccountProfile, google_photo_url


def test_empty_profile_has_only_optional_display_fields() -> None:
    assert AccountProfile().model_dump() == {
        "display_name": None,
        "email": None,
        "photo_url": None,
    }
    with pytest.raises(ValidationError):
        AccountProfile.model_validate({"uid": "not-a-profile-field"})
    with pytest.raises(ValidationError):
        AccountProfile.model_validate({"email": 1})


@pytest.mark.parametrize("value", [None, "", "  ", "\n", "x" * 321])
def test_missing_or_unusable_display_text_is_absent(value: str | None) -> None:
    assert AccountProfile(display_name=value).display_name is None


def test_display_text_is_trimmed_but_not_invented() -> None:
    assert AccountProfile(display_name=" A ").display_name == "A"
    assert AccountProfile(email=" ").email is None


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "http://lh3.googleusercontent.com/a",
        "https://lh3.googleusercontent.com.evil.invalid/a",
        "https://evil.invalid/a",
        "https://googleusercontent.com/a",
        "https://lh3.googleusercontent.com@evil.invalid/a",
        "https://user@lh3.googleusercontent.com/a",
        "https://lh3.googleusercontent.com:8443/a",
        "https://lh3.googleusercontent.com/a#fragment",
        "https://lh3.googleusercontent.com/a\n",
        "//lh3.googleusercontent.com/a",
        "data:image/png;base64,",
    ],
)
def test_untrusted_photo_urls_are_absent(value: str | None) -> None:
    assert google_photo_url(value) is None
    assert AccountProfile(photo_url=value).photo_url is None


@pytest.mark.parametrize(
    "host", ["lh3.googleusercontent.com", "lh6.googleusercontent.com"]
)
def test_google_avatar_host_primitive(host: str) -> None:
    value = f"https://{host}/a"
    assert google_photo_url(value) == value
