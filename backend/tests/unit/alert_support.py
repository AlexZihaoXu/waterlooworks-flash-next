"""Structural pipeline checkpoints for alert readiness tests.

Generic IDs and placeholder labels only; every row goes through the real
schema (content checkpoints use the production storage functions).
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from waterlooworks_flash_next.content import StoredPostingContent
from waterlooworks_flash_next.content_storage import (
    complete_content_attempt,
    record_content_request,
    start_content_attempt,
)
from waterlooworks_flash_next.database import open_database


def structural_content(
    posting_id: str, summary: str = "Structural"
) -> StoredPostingContent:
    return StoredPostingContent(
        id=posting_id,
        fields={
            "Job Title:": "Title",
            "Organization:": "Organization",
            "Work Term:": "Term",
            "Job Summary:": summary,
        },
    )


def collect(path: Path, run_id: str, *ids: str, summary: str = "Structural") -> None:
    """Save detail content for postings through a running collection."""
    connection = open_database(path)
    try:
        for posting_id in ids:
            attempt = start_content_attempt(connection, run_id, posting_id)
            record_content_request(connection, attempt, retry=False)
            complete_content_attempt(
                connection, attempt, structural_content(posting_id, summary)
            )
    finally:
        connection.close()


_job_counter = 0


def analysed(
    path: Path,
    posting_id: str,
    status: str,
    *,
    error_category: str | None = None,
    content_hash: str | None = None,
) -> None:
    """Record one finished inference item for the posting's saved content."""
    global _job_counter
    _job_counter += 1
    job_id = f"job-{_job_counter}"
    now = datetime.now(UTC).isoformat()
    connection = open_database(path)
    try:
        with connection:
            listing = connection.execute(
                "SELECT revision_id,content_hash FROM posting_revisions "
                "WHERE posting_id=? ORDER BY revision_id DESC LIMIT 1",
                (posting_id,),
            ).fetchone()
            content = connection.execute(
                "SELECT revision_id,content_hash FROM posting_content_revisions "
                "WHERE posting_id=? ORDER BY revision_id DESC LIMIT 1",
                (posting_id,),
            ).fetchone()
            projection = None
            if status == "accepted":
                projection = connection.execute(
                    "INSERT INTO analysis_projections(posting_id,listing_revision_id,"
                    "content_revision_id,listing_hash,content_hash,producer_kind,"
                    "producer_name,producer_version,parser_version,created_at) "
                    "VALUES (?,?,?,?,?,'model','structural','1','1',?)",
                    (posting_id, listing[0], content[0], listing[1], content[1], now),
                ).lastrowid
            connection.execute(
                "INSERT INTO inference_jobs(job_id,status,model_id,endpoint,"
                "input_version,template_version,validator_version,max_tokens,"
                "concurrency,max_attempts,timeout_seconds,created_at,finished_at) "
                "VALUES (?,'completed','pi:structural','http://127.0.0.1:1',"
                "'1','1','1',100,1,1,60,?,?)",
                (job_id, now, now),
            )
            connection.execute(
                "INSERT INTO inference_job_items(job_id,posting_id,ordinal,"
                "selected_listing_revision_id,selected_content_revision_id,"
                "selected_listing_hash,selected_content_hash,input_hash,"
                "request_compensation,request_skills,status,projection_id,"
                "error_category,finished_at) VALUES (?,?,0,?,?,?,?,?,1,1,?,?,?,?)",
                (
                    job_id,
                    posting_id,
                    listing[0],
                    content[0],
                    listing[1],
                    content_hash or content[1],
                    "0" * 64,
                    status,
                    projection,
                    error_category,
                    None if status in ("pending", "running") else now,
                ),
            )
    finally:
        connection.close()


def first_seen(path: Path, posting_id: str, when: datetime) -> None:
    """Move a posting's first sighting (structural clock control)."""
    connection = sqlite3.connect(path)
    try:
        with connection:
            connection.execute(
                "UPDATE postings SET first_seen_at=? WHERE posting_id=?",
                (when.isoformat(), posting_id),
            )
    finally:
        connection.close()
