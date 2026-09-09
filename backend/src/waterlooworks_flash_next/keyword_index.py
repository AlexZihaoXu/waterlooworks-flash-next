"""Source-current, read-only input for browser-worker keyword coverage."""

import hashlib
import json
import sqlite3

from waterlooworks_flash_next.api_models import (
    KeywordIndexPosting,
    KeywordIndexResponse,
)
from waterlooworks_flash_next.content import StoredPostingContent
from waterlooworks_flash_next.source_enrichment import read_source_enrichment


def read_keyword_index(
    connection: sqlite3.Connection, if_none_match: str | None = None
) -> tuple[KeywordIndexResponse | None, str]:
    """Read one consistent snapshot; never create extraction work."""
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    try:
        run = connection.execute(
            "SELECT run_id,configuration_hash,configuration_json "
            "FROM source_extraction_runs r "
            "WHERE EXISTS (SELECT 1 FROM source_extraction_items i "
            "WHERE i.run_id=r.run_id AND i.mode='keywords') "
            "ORDER BY created_at DESC,run_id DESC LIMIT 1"
        ).fetchone()
        total = int(connection.execute("SELECT count(*) FROM postings").fetchone()[0])
        run_id: str | None = run["run_id"] if run else None
        rows = connection.execute(
            "SELECT i.posting_id,i.content_hash,i.part,i.status,i.extraction_id,"
            "i.rejected_count,i.content_revision_id,c.content_hash current_hash "
            "FROM source_extraction_items i LEFT JOIN posting_contents c "
            "ON c.posting_id=i.posting_id WHERE i.run_id=? AND i.mode='keywords' "
            "ORDER BY i.posting_id,i.part",
            (run_id,),
        ).fetchall()
        term_generation = connection.execute(
            "SELECT count(*),max(rowid) FROM source_extraction_terms"
        ).fetchone()
        # Include append generation as well as immutable extraction/source identity.
        revision = hashlib.sha256(
            json.dumps(
                [
                    list(run) if run else None,
                    total,
                    list(term_generation),
                    [list(row) for row in rows],
                ],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        etag = f'"{revision}"'
        if if_none_match == etag:
            return None, etag
        source = read_source_enrichment(connection)
        eligible = {
            posting_id: entry
            for posting_id, entry in source.items()
            if entry.keywords_status.state not in ("not_requested", "pending", "stale")
        }
        entries = []
        if eligible:
            placeholders = ",".join("?" for _ in eligible)
            for row in connection.execute(
                "SELECT posting_id,content_hash,content_json FROM posting_contents "
                f"WHERE posting_id IN ({placeholders}) ORDER BY posting_id",
                tuple(eligible),
            ):
                content = StoredPostingContent.model_validate_json(row["content_json"])
                entries.append(
                    KeywordIndexPosting(
                        posting_id=row["posting_id"],
                        content_hash=row["content_hash"],
                        fields=list(content.fields.values()),
                        keywords=[k.name for k in eligible[row["posting_id"]].keywords],
                        partial=eligible[row["posting_id"]].keywords_status.state
                        not in ("accepted", "empty"),
                    )
                )
        return KeywordIndexResponse(
            revision=revision, run_id=run_id, total_postings=total, entries=entries
        ), etag
    finally:
        connection.rollback()
