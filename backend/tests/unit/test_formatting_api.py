"""Posting detail responses carry optional formatting beside the plain text.

Structural rows written through the production storage functions; generic
IDs and placeholder words only. No network.
"""

from pathlib import Path

from alert_support import structural_content
from auth_support import session_client
from test_agent_api import BASE, agent_client

from waterlooworks_flash_next.content import StoredPostingFormatting
from waterlooworks_flash_next.content_storage import (
    complete_content_attempt,
    record_content_request,
    start_content_attempt,
)
from waterlooworks_flash_next.database import (
    StoredListing,
    open_database,
    save_listings,
    start_collection_run,
)

BLOCKS = [
    {
        "type": "paragraph",
        "content": [
            {"type": "text", "text": "Struct", "bold": False, "italic": True},
            {
                "type": "link",
                "href": "https://example.com/a",
                "text": "ural",
                "bold": True,
                "italic": False,
            },
        ],
    }
]


def _saved(path: Path, formatted: bool) -> None:
    connection = open_database(path)
    try:
        run_id = start_collection_run(connection)
        save_listings(
            connection,
            run_id,
            [StoredListing(id=pid, title="Title") for pid in ("1", "2")],
        )
        for posting_id in ("1", "2"):
            attempt = start_content_attempt(connection, run_id, posting_id)
            record_content_request(connection, attempt, retry=False)
            complete_content_attempt(
                connection,
                attempt,
                structural_content(posting_id),
                StoredPostingFormatting.model_validate(
                    {"fields": {"Job Summary:": BLOCKS}}
                )
                if formatted and posting_id == "1"
                else None,
            )
    finally:
        connection.close()


def test_detail_includes_formatting_and_falls_back_to_plain_text(
    tmp_path: Path,
) -> None:
    path = tmp_path / "db.sqlite3"
    _saved(path, formatted=True)
    with session_client(path) as client:
        formatted = client.get("/api/postings/1").json()
        plain = client.get("/api/postings/2").json()
    assert formatted["content"]["fields"]["Job Summary:"] == "Structural"
    assert formatted["formatting"] == {
        "version": "rich-v1",
        "fields": {"Job Summary:": BLOCKS},
    }
    assert plain["content"]["fields"]["Job Summary:"] == "Structural"
    assert plain["formatting"] is None


def test_agent_detail_includes_the_same_optional_formatting(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    _saved(path, formatted=True)
    with agent_client(path) as (client, _token):
        formatted = client.get(f"{BASE}/postings/1").json()
        plain = client.get(f"{BASE}/postings/2").json()
    assert formatted["formatting"]["fields"] == {"Job Summary:": BLOCKS}
    assert plain["formatting"] is None


def test_detail_contract_documents_formatting_as_optional_blocks(
    tmp_path: Path,
) -> None:
    from waterlooworks_flash_next.api import create_app

    schemas = create_app(tmp_path / "db.sqlite3").openapi()["components"]["schemas"]
    detail = schemas["JobDetailResponse"]["properties"]["formatting"]
    assert {"type": "null"} in detail["anyOf"]
    link = schemas["LinkRun"]
    assert set(link["properties"]) == {"type", "href", "text", "bold", "italic"}
    assert schemas["ListBlock"]["properties"]["ordered"]["type"] == "boolean"
