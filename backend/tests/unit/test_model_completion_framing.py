"""Strict JSON framing using empty containers, not provider fixtures."""

import pytest

from waterlooworks_flash_next.model_validation import (
    ModelOutputError,
    parse_completion_text,
)

EMPTY = '{"base_rates":[],"skills":[]}'


@pytest.mark.parametrize(
    "language,newline", [("json", "\n"), ("", "\n"), ("JSON", "\r\n")]
)
def test_single_enclosing_json_fence_is_only_transport_framing(
    language: str, newline: str
) -> None:
    parsed = parse_completion_text(
        f"</think>  ```{language}{newline}{EMPTY}{newline}```  "
    )
    assert parsed.base_rates == [] and parsed.skills == []


@pytest.mark.parametrize(
    "final",
    [
        f"prose\n```json\n{EMPTY}\n```",
        f"```json\n{EMPTY}\n```\nprose",
        f"```json\n{EMPTY}\n```\n```json\n{EMPTY}\n```",
        f"```python\n{EMPTY}\n```",
        f"```json\n{EMPTY}",
        '```json\n{"base_rates":[],"base_rates":[],"skills":[]}\n```',
        '```json\n{"base_rates":[],"skills":[],"extra":null}\n```',
        '```json\n{"base_rates":[],"skills":[],"extra":NaN}\n```',
        '```json\n{"base_rates":[],"skills":[],"extra":"<|im_end|>"}\n```',
    ],
)
def test_fence_does_not_relax_json_schema_or_content_guards(final: str) -> None:
    with pytest.raises(ModelOutputError):
        parse_completion_text("</think>" + final)
