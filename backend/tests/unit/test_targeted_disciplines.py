"""Targeted string primitives and an empty real database, never posting fixtures."""

from pathlib import Path

from waterlooworks_flash_next.database import open_database
from waterlooworks_flash_next.targeted_disciplines import (
    read_targeted_disciplines,
    split_targeted_disciplines,
)


def test_split_targeted_disciplines() -> None:
    assert split_targeted_disciplines(None) == []
    assert split_targeted_disciplines(
        " Targeted Clusters\nView Targeted Degrees and Disciplines\n"
        "Not captured in saved content\n ENG - Electrical and Computer Engineering \n"
        "ENG - Electrical and Computer Engineering\nunknown\n"
    ) == ["ENG - Electrical and Computer Engineering", "unknown"]


def test_invalid_targeted_value_is_sanitized() -> None:
    import pytest

    for value in [42, [], {}]:
        with pytest.raises(ValueError, match="Invalid stored targeted disciplines"):
            split_targeted_disciplines(value)


def test_empty_database_has_no_targeted_values(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "empty.sqlite3")
    try:
        assert read_targeted_disciplines(connection) == {}
    finally:
        connection.close()
