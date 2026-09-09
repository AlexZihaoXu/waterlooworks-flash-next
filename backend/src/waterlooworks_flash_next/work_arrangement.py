"""Read explicit arrangements from current saved content, without inference."""

import sqlite3
from typing import Literal, cast

Arrangement = Literal["Remote", "Hybrid", "In-person"]


def arrangement_value(value: object) -> Arrangement | None:
    text = value.strip() if isinstance(value, str) else None
    return (
        cast(Arrangement, text) if text in ("Remote", "Hybrid", "In-person") else None
    )


def read_work_arrangements(connection: sqlite3.Connection) -> dict[str, Arrangement]:
    result: dict[str, Arrangement] = {}
    for posting_id, raw in connection.execute(
        """SELECT posting_id,
        json_extract(content_json, '$.fields."Employment Location Arrangement:"')
        FROM posting_contents"""
    ):
        value = arrangement_value(raw)
        if value is not None:
            result[posting_id] = value
    return result
