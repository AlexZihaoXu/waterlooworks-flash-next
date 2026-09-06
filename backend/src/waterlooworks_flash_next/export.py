"""Private listing-only JSON exports; never serialize the runtime request source."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from pydantic import JsonValue

from waterlooworks_flash_next.listings import ListingCollection

EXPORT_FIELDS = {
    "Id": "id",
    "JobTitle": "title",
    "Organization": "organization",
    "Division": "division",
    "Openings": "openings",
    "City": "city",
    "Level": "level",
    "ApplicationCount": "application_count",
    "Deadline": "deadline",
}


def write_private_json(payload: dict[str, JsonValue], directory: Path) -> Path:
    """Write atomically using a private temporary file and unique final filename."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output = directory / f"listings-{timestamp}-{uuid4().hex[:8]}.json"
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=directory, suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(output)
        return output
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def export_listings(collection: ListingCollection, directory: Path) -> Path:
    """Export confirmed display columns only, after a completed collection pass."""
    if not collection.complete:
        raise ValueError("Cannot export an incomplete collection as a complete result.")
    postings: list[JsonValue] = []
    for posting_id, row in collection.postings.items():
        posting: dict[str, JsonValue] = {"id": posting_id}
        for cell in row.data:
            if cell.key not in EXPORT_FIELDS or cell.key == "Id":
                continue
            if cell.key == "JobTitle":
                if not isinstance(cell.value, dict) or not isinstance(
                    cell.value.get("postingTitle"), str
                ):
                    raise ValueError("Unexpected listing title structure.")
                posting["title"] = cell.value["postingTitle"]
            else:
                posting[EXPORT_FIELDS[cell.key]] = cell.value
        postings.append(posting)
    return write_private_json(
        {
            "exported_at": datetime.now(UTC).isoformat(),
            "complete": True,
            "pages_completed": collection.pages_completed,
            "records_received": collection.records_received,
            "unique_postings": len(postings),
            "duplicates": collection.duplicates,
            "postings": postings,
        },
        directory,
    )
