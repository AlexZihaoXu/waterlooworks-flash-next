"""Allowlisted, ephemeral display fields; never session or authorization claims."""

from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

GOOGLE_AVATAR_HOSTS = frozenset(f"lh{i}.googleusercontent.com" for i in range(3, 7))


def google_photo_url(value: str | None) -> str | None:
    if (
        not value
        or len(value) > 2048
        or "\\" in value
        or "#" in value
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        return None
    try:
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or url.netloc not in GOOGLE_AVATAR_HOSTS
            or not url.path.startswith("/")
            or url.fragment
        ):
            return None
    except ValueError:
        return None
    return value


class AccountProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    display_name: str | None = Field(default=None, repr=False)
    email: str | None = Field(default=None, repr=False)
    photo_url: str | None = Field(default=None, repr=False)

    @field_validator("display_name", "email")
    @classmethod
    def display_text(cls, value: str | None) -> str | None:
        if value is None or len(value) > 320:
            return None
        value = value.strip()
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            return None
        return value

    @field_validator("photo_url")
    @classmethod
    def trusted_photo(cls, value: str | None) -> str | None:
        return google_photo_url(value)
