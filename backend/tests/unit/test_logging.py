"""Credential-free formatting checks; no browser, fixtures, or mock data."""

import logging
import re

import pytest

from waterlooworks_flash_next.logging import StageFormatter


@pytest.mark.parametrize(
    ("level", "label", "color"),
    [
        (logging.DEBUG, "DEBUG", "\x1b[90m"),
        (logging.INFO, "INFO", "\x1b[92m"),
        (logging.WARNING, "WARN", "\x1b[93m"),
        (logging.ERROR, "ERROR", "\x1b[91m"),
    ],
)
def test_levels_have_timestamp_scope_and_color(
    level: int, label: str, color: str
) -> None:
    record = logging.LogRecord("login", level, "", 0, "Stage complete", (), None)
    plain = StageFormatter(color=False).format(record)
    assert re.fullmatch(
        rf"\[\d{{2}}:\d{{2}}:\d{{2}}\] \[login/{label}\]: Stage complete", plain
    )
    colored = StageFormatter(color=True).format(record)
    assert f"{color}{label}\x1b[0m" in colored
    assert re.sub(r"\x1b\[[0-9;]*m", "", colored) == plain


def test_exception_and_stack_details_are_not_rendered() -> None:
    record = logging.LogRecord("login", logging.ERROR, "", 0, "Stage failed", (), None)
    record.exc_text = "Exception details must not be emitted"
    record.stack_info = "Stack details must not be emitted"
    result = StageFormatter(color=False).format(record)
    assert result.endswith("[login/ERROR]: Stage failed")
    assert "details" not in result
