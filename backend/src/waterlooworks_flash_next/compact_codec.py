"""Versioned lossless JSON patches; linear text splices, bounded zlib decoding."""

import hashlib
import json
import zlib
from typing import cast

from pydantic import JsonValue

MAX_BYTES = 64 * 1024 * 1024
MAX_JSON_DEPTH = 64


class HistoryError(ValueError):
    """Safe corruption/limit error, never include source payloads."""


def _depth(value: JsonValue) -> None:
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > MAX_JSON_DEPTH:
            raise HistoryError("History JSON nesting limit exceeded.")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)


def canonical_text(value: JsonValue) -> str:
    try:
        _depth(value)
        text = json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(text.encode("utf-8")) > MAX_BYTES:
            raise HistoryError("History payload size limit exceeded.")
        return text
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise HistoryError("Invalid or oversized history JSON.") from None


def _parse(text: str) -> JsonValue:
    try:
        if len(text.encode("utf-8")) > MAX_BYTES:
            raise ValueError
        value = cast(JsonValue, json.loads(text))
        if canonical_text(value) != text:
            raise ValueError
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise HistoryError("History JSON is not valid canonical evidence.") from None


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def verify_snapshot(text: str, expected_hash: str) -> str:
    _parse(text)
    if digest(text) != expected_hash:
        raise HistoryError("History snapshot hash mismatch.")
    return text


def compress(text: str) -> bytes:
    try:
        raw = text.encode("utf-8")
        if len(raw) > MAX_BYTES:
            raise ValueError
        encoded = zlib.compress(raw)
        if len(encoded) > MAX_BYTES:
            raise ValueError
        return encoded
    except (ValueError, UnicodeError):
        raise HistoryError("History payload size limit exceeded.") from None


def decompress(encoded: bytes, expected_size: int, *, limit: int = MAX_BYTES) -> str:
    """Exactly one complete stream; never flush unbounded or accept trailing data."""
    try:
        if not 0 <= expected_size <= limit <= MAX_BYTES or len(encoded) > MAX_BYTES:
            raise ValueError
        decoder = zlib.decompressobj()
        raw = decoder.decompress(encoded, expected_size + 1)
        if (
            len(raw) != expected_size
            or not decoder.eof
            or decoder.unused_data
            or decoder.unconsumed_tail
        ):
            raise ValueError
        return raw.decode("utf-8")
    except (ValueError, UnicodeError, zlib.error):
        raise HistoryError("Corrupt or oversized compressed history.") from None


def _patch(before: JsonValue, after: JsonValue) -> JsonValue:
    if canonical_text(before) == canonical_text(after):
        return ["same"]
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[JsonValue] = []
        for key in sorted(before.keys() | after.keys()):
            if key not in before:
                changes.append([key, "add", after[key]])
            elif key not in after:
                changes.append([key, "remove", before[key]])
            elif canonical_text(before[key]) != canonical_text(after[key]):
                changes.append([key, "change", _patch(before[key], after[key])])
        return ["object", changes]
    if isinstance(before, str) and isinstance(after, str):
        prefix = 0
        bound = min(len(before), len(after))
        while prefix < bound and before[prefix] == after[prefix]:
            prefix += 1
        suffix = 0
        while suffix < bound - prefix and before[-suffix - 1] == after[-suffix - 1]:
            suffix += 1
        return [
            "text",
            prefix,
            suffix,
            before[prefix : len(before) - suffix],
            after[prefix : len(after) - suffix],
        ]
    return ["replace", before, after]


def make_delta(before: str, after: str) -> str:
    return canonical_text(
        {
            "version": 1,
            "before": digest(before),
            "after": digest(after),
            "patch": _patch(_parse(before), _parse(after)),
        }
    )


def _apply(
    value: JsonValue, patch: JsonValue, reverse: bool, depth: int = 0
) -> JsonValue:
    if depth > MAX_JSON_DEPTH or not isinstance(patch, list) or not patch:
        raise ValueError
    operation = patch[0]
    if operation == "same" and len(patch) == 1:
        return value
    if operation == "replace" and len(patch) == 3:
        old, new = (patch[2], patch[1]) if reverse else (patch[1], patch[2])
        if canonical_text(value) != canonical_text(old):
            raise ValueError
        return new
    if operation == "text" and len(patch) == 5 and isinstance(value, str):
        prefix, suffix = patch[1:3]
        old, new = (patch[4], patch[3]) if reverse else (patch[3], patch[4])
        if (
            type(prefix) is not int
            or type(suffix) is not int
            or not isinstance(old, str)
            or not isinstance(new, str)
        ):
            raise ValueError
        if prefix < 0 or suffix < 0 or prefix + suffix > len(value):
            raise ValueError
        if value[prefix : len(value) - suffix] != old:
            raise ValueError
        if len(value) - len(old) + len(new) > MAX_BYTES:
            raise ValueError
        return value[:prefix] + new + (value[-suffix:] if suffix else "")
    if operation == "object" and len(patch) == 2 and isinstance(value, dict):
        changes = patch[1]
        if not isinstance(changes, list):
            raise ValueError
        result = dict(value)
        seen: set[str] = set()
        for change in changes:
            if not isinstance(change, list) or len(change) != 3:
                raise ValueError
            key, action, item = change
            if not isinstance(key, str) or key in seen:
                raise ValueError
            seen.add(key)
            if reverse and action in ("add", "remove"):
                action = "remove" if action == "add" else "add"
            if action == "add" and key not in result:
                result[key] = item
            elif action == "remove" and key in result:
                if canonical_text(result[key]) != canonical_text(item):
                    raise ValueError
                del result[key]
            elif action == "change" and key in result:
                result[key] = _apply(result[key], item, reverse, depth + 1)
            else:
                raise ValueError
        return result
    raise ValueError


def apply_delta(source: str, encoded: str, *, reverse: bool = False) -> str:
    """Verify the input and output hashes in either direction; fail closed."""
    try:
        patch = _parse(encoded)
        if (
            not isinstance(patch, dict)
            or set(patch) != {"version", "before", "after", "patch"}
            or type(patch["version"]) is not int
            or patch["version"] != 1
        ):
            raise ValueError
        before, after = (
            (patch["after"], patch["before"])
            if reverse
            else (patch["before"], patch["after"])
        )
        if not isinstance(before, str) or not isinstance(after, str):
            raise ValueError
        value = _parse(verify_snapshot(source, before))
        result = canonical_text(_apply(value, patch["patch"], reverse))
        return verify_snapshot(result, after)
    except (ValueError, TypeError, KeyError, RecursionError):
        raise HistoryError("Corrupt reversible history delta.") from None
