"""Export safety checks using empty data only, never mock postings or credentials."""

import json
from pathlib import Path

from waterlooworks_flash_next.export import EXPORT_FIELDS, write_private_json


def test_export_allowlist_excludes_request_and_action_fields() -> None:
    assert set(EXPORT_FIELDS) == {
        "Id",
        "JobTitle",
        "Organization",
        "Division",
        "Openings",
        "City",
        "Level",
        "ApplicationCount",
        "Deadline",
    }
    assert not {"action", "apply", "Cookie", "password", "username"} & set(
        EXPORT_FIELDS
    )


def test_empty_export_is_valid_json_and_does_not_overwrite(tmp_path: Path) -> None:
    first = write_private_json({"postings": []}, tmp_path)
    second = write_private_json({"postings": []}, tmp_path)
    assert first != second
    assert json.loads(first.read_text(encoding="utf-8")) == {"postings": []}
    assert json.loads(second.read_text(encoding="utf-8")) == {"postings": []}
    assert first.suffix == ".json"
    assert len(list(tmp_path.iterdir())) == 2
