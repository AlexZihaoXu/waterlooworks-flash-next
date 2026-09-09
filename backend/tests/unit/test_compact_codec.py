"""Generic JSON/text codec contracts, never posting or API fixtures."""

import hashlib
import zlib

import pytest
from pydantic import JsonValue

from waterlooworks_flash_next.compact_codec import (
    HistoryError,
    apply_delta,
    canonical_text,
    compress,
    decompress,
    make_delta,
)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ({}, {"a": None}),
        ({"a": 1, "b": [None, True]}, {"a": 1.0, "b": [False]}),
        ({"a": -0.0}, {"a": 0.0}),
        ({"a": {"b": "αβγ"}}, {"a": {"b": "α🙂γ"}, "c": None}),
        ({"a": None}, {}),
        ([1, "1"], [1.0, None]),
    ],
)
def test_structural_and_text_deltas_are_exactly_reversible(
    before: JsonValue, after: JsonValue
) -> None:
    first, second = canonical_text(before), canonical_text(after)
    patch = make_delta(first, second)
    assert apply_delta(first, patch) == second
    assert apply_delta(second, patch, reverse=True) == first
    assert decompress(compress(second), len(second.encode())) == second


def test_large_text_uses_linear_splice() -> None:
    first = canonical_text({"text": "x" * 100000 + "a" + "y" * 99999})
    second = canonical_text({"text": "x" * 100000 + "b" + "y" * 99999})
    patch = make_delta(first, second)
    assert len(patch) < 400
    assert apply_delta(first, patch) == second
    assert apply_delta(second, patch, reverse=True) == first


def test_delta_rejects_wrong_base_target_and_malformed_patch() -> None:
    first, second = canonical_text({"a": None}), canonical_text({})
    patch = make_delta(first, second)
    for source, supplied in [
        (second, patch),
        (first, patch[:-1]),
        (first, patch.replace(hashlib.sha256(second.encode()).hexdigest(), "0" * 64)),
    ]:
        with pytest.raises(HistoryError):
            apply_delta(source, supplied)


def test_compression_rejects_truncation_trailing_stream_and_size_bombs() -> None:
    encoded = compress("x" * 2000)
    for value in [encoded[:-1], encoded + b"trailing", encoded + encoded, b"invalid"]:
        with pytest.raises(HistoryError):
            decompress(value, 2000)
    with pytest.raises(HistoryError):
        decompress(encoded, 100, limit=100)
    with pytest.raises(HistoryError):
        decompress(zlib.compress(b"\xff"), 1)


def test_nonfinite_and_overdeep_json_fail_safely() -> None:
    with pytest.raises(HistoryError):
        canonical_text(float("nan"))
    with pytest.raises(HistoryError):
        make_delta("{}", "[" * 100 + "0" + "]" * 100)


@pytest.mark.parametrize(
    "node",
    [
        ["text", -1, 0, "", ""],
        ["text", True, 0, "", ""],
        ["text", 100, 100, "", ""],
        ["replace", "wrong", "new"],
        ["unknown"],
    ],
)
def test_malformed_patch_operations_fail_even_with_valid_envelope(
    node: JsonValue,
) -> None:
    from waterlooworks_flash_next.compact_codec import digest

    source = canonical_text("value")
    patch = canonical_text(
        {"version": 1, "before": digest(source), "after": digest(source), "patch": node}
    )
    with pytest.raises(HistoryError):
        apply_delta(source, patch)


def test_duplicate_object_operations_and_noncanonical_json_are_rejected() -> None:
    from waterlooworks_flash_next.compact_codec import digest

    source = canonical_text({})
    patch = canonical_text(
        {
            "version": 1,
            "before": digest(source),
            "after": digest(source),
            "patch": ["object", [["a", "add", None], ["a", "remove", None]]],
        }
    )
    with pytest.raises(HistoryError):
        apply_delta(source, patch)
    with pytest.raises(HistoryError):
        make_delta(source, '{"a":1,"a":2}')
