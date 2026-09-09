"""Compact saved targeted values; no detail retrieval or inferred equivalences."""

import sqlite3

_LEGACY_MARKERS = frozenset(
    {
        "Targeted Clusters",
        "View Targeted Degrees and Disciplines",
        "Not captured in saved content",
    }
)


def split_targeted_disciplines(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, str) or len(value) > 200_000:
        raise ValueError("Invalid stored targeted disciplines.")
    return list(
        dict.fromkeys(
            line
            for raw in value.splitlines()
            if (line := raw.strip()) and line not in _LEGACY_MARKERS
        )
    )


def read_targeted_disciplines(connection: sqlite3.Connection) -> dict[str, list[str]]:
    """One batched projection; neither full content nor private diagnostics escape."""
    try:
        result: dict[str, list[str]] = {}
        for posting_id, kind, value in connection.execute(
            """SELECT posting_id,
            json_type(
                content_json, '$.fields."Targeted Degrees and Disciplines:"'
            ),
            json_extract(
                content_json, '$.fields."Targeted Degrees and Disciplines:"'
            )
            FROM posting_contents"""
        ):
            if kind not in (None, "null", "text"):
                raise ValueError("Invalid stored targeted disciplines.")
            result[posting_id] = split_targeted_disciplines(value)
        return result
    except Exception:
        raise ValueError("Stored targeted disciplines unavailable.") from None
