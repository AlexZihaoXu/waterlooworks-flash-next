"""Explicit resumable loopback Qwen extraction for already saved postings."""

import argparse
import concurrent.futures
import hashlib
import json
import sqlite3
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast
from uuid import UUID, uuid4

from pydantic import Field

from waterlooworks_flash_next.compact_history import revision_snapshot
from waterlooworks_flash_next.content import StoredPostingContent
from waterlooworks_flash_next.database import open_database
from waterlooworks_flash_next.model_validation import (
    INPUT_VERSION,
    TEMPLATE_VERSION,
    VALIDATOR_VERSION,
    ModelOutputError,
    ValidatedExtraction,
    build_raw_prompt,
    parse_completion_text,
    validate_extraction,
)
from waterlooworks_flash_next.normalized_storage import decimal_to_storage
from waterlooworks_flash_next.parser_models import ParserModel

MODEL_ID = "qwen3.8-27b@q4_k_m"
MODEL_NAME = "qwen3.8-27b"
MODEL_VERSION = "q4_k_m"
ENDPOINT = "http://127.0.0.1:1234"
MAX_TOKENS = 4096
TIMEOUT_SECONDS = 600
LEASE_SECONDS = TIMEOUT_SECONDS + 60
MAX_JOB_POSTINGS = 10_000
MAX_BODY_BYTES = 4 * 1024 * 1024
JobStatus = Literal[
    "pending", "running", "stopping", "completed", "failed", "interrupted"
]


class InferenceError(Exception):
    """Safe failure without source values or provider response text."""


class _OwnershipLost(Exception):
    """The persisted lease no longer belongs to this runner."""


class _ProviderError(Exception):
    def __init__(self, category: str, retryable: bool = False) -> None:
        super().__init__(category)
        self.category = category
        self.retryable = retryable


class InferenceConfig(ParserModel):
    concurrency: int = Field(default=4, ge=1, le=4)
    max_attempts: int = Field(default=2, ge=1, le=3)


@dataclass(frozen=True)
class InferenceJobStatus:
    job_id: str
    persisted_status: JobStatus
    effective_status: JobStatus
    stop_requested: bool
    counts: dict[str, int]
    error_category: str | None


@dataclass(frozen=True)
class _Item:
    job_id: str
    posting_id: str
    content_hash: str
    input_hash: str
    fields: dict[str, str]
    prompt: str
    request_compensation: bool
    request_skills: bool
    deterministic_skills: tuple[str, ...]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        return None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _renew_lease(
    connection: sqlite3.Connection, job_id: str, current_lease: str
) -> str:
    """Renew only the lease token held by this runner inside its transaction."""
    if not connection.in_transaction:
        raise InferenceError("Inference heartbeat requires a transaction.")
    renewed = (datetime.now(UTC) + timedelta(seconds=LEASE_SECONDS)).isoformat(
        timespec="microseconds"
    )
    changed = connection.execute(
        "UPDATE inference_jobs SET lease_expires_at=? WHERE job_id=? "
        "AND status IN ('running','stopping') AND lease_expires_at=?",
        (renewed, job_id, current_lease),
    )
    if changed.rowcount != 1:
        raise _OwnershipLost
    return renewed


def _pipeline_version() -> str:
    value = (
        f"{MODEL_ID}|{INPUT_VERSION}|{TEMPLATE_VERSION}|"
        f"{VALIDATOR_VERSION}|{MAX_TOKENS}"
    )
    return f"{VALIDATOR_VERSION}-{hashlib.sha256(value.encode()).hexdigest()[:12]}"


def _request(path: str, body: dict[str, object] | None = None) -> object:
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    if data is not None and len(data) > MAX_BODY_BYTES:
        raise _ProviderError("configuration")
    request = urllib.request.Request(
        ENDPOINT + path,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST" if data is not None else "GET",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            declared = response.headers.get("Content-Length")
            if declared is not None and int(declared) > MAX_BODY_BYTES:
                raise _ProviderError("response_size")
            raw = response.read(MAX_BODY_BYTES + 1)
            if len(raw) > MAX_BODY_BYTES:
                raise _ProviderError("response_size")
    except urllib.error.HTTPError as error:
        if error.code == 429 or error.code >= 500:
            raise _ProviderError("http_server", retryable=True) from None
        raise _ProviderError("http_client") from None
    except TimeoutError:
        raise _ProviderError("timeout", retryable=True) from None
    except (urllib.error.URLError, ConnectionError, OSError, ValueError):
        raise _ProviderError("connection", retryable=True) from None
    try:
        return json.loads(
            raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError())
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise _ProviderError("response_schema") from None


def _verify_model() -> None:
    payload = _request("/api/v1/models")
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise _ProviderError("response_schema")
    loaded = [
        model
        for model in payload["models"]
        if isinstance(model, dict) and model.get("loaded_instances")
    ]
    if len(loaded) != 1 or loaded[0].get("key") != MODEL_ID:
        raise _ProviderError("model_unavailable")


def _complete(item: _Item) -> ValidatedExtraction:
    payload = _request(
        "/v1/completions",
        {
            "model": MODEL_ID,
            "prompt": item.prompt,
            "temperature": 0,
            "max_tokens": MAX_TOKENS,
            "stop": ["<|im_end|>", "<|endoftext|>"],
        },
    )
    try:
        choices = payload["choices"]  # type: ignore[index]
        choice = choices[0]
        if len(choices) != 1 or choice["finish_reason"] != "stop":
            raise TypeError
        candidate = parse_completion_text(choice["text"])
        return validate_extraction(
            candidate,
            item.fields,
            source_hash=item.content_hash,
            request_compensation=item.request_compensation,
            request_skills=item.request_skills,
            deterministic_skills=item.deterministic_skills,
        )
    except ModelOutputError as error:
        category = (
            "thinking_boundary"
            if "thinking boundary" in str(error)
            else "evidence_rejected"
        )
        raise _ProviderError(category) from None
    except (KeyError, IndexError, TypeError):
        raise _ProviderError("response_schema") from None


def _source(
    connection: sqlite3.Connection, posting_id: str
) -> tuple[int, int, str, str, StoredPostingContent, bool, bool, tuple[str, ...]]:
    row = connection.execute(
        """SELECT
             (SELECT max(revision_id) FROM posting_revisions
                WHERE posting_id=pointer.posting_id) AS listing_revision_id,
             (SELECT max(revision_id) FROM posting_content_revisions
                WHERE posting_id=pointer.posting_id) AS content_revision_id,
             postings.content_hash AS listing_hash,
             contents.content_hash,
             pointer.deterministic_projection_id,
             results.compensation_state,results.skills_state
           FROM posting_projection_current pointer
           JOIN analysis_projections deterministic
             ON deterministic.projection_id=pointer.deterministic_projection_id
           JOIN normalized_projection_results results
             ON results.projection_id=pointer.deterministic_projection_id
           JOIN postings USING(posting_id)
           JOIN posting_contents contents USING(posting_id)
           WHERE pointer.posting_id=?
             AND deterministic.content_hash=contents.content_hash""",
        (posting_id,),
    ).fetchone()
    if row is None or row[0] is None or row[1] is None:
        raise InferenceError("Current normalized source is unavailable.")
    content = StoredPostingContent.model_validate_json(
        revision_snapshot(connection, "content", row[1])
    )
    skills = tuple(
        skill[0]
        for skill in connection.execute(
            """SELECT catalog.canonical_name
               FROM normalized_skill_mentions mention
               JOIN normalized_skills catalog USING(skill_id)
               WHERE mention.projection_id=? AND mention.excluded_reason IS NULL
               ORDER BY mention.ordinal""",
            (row[4],),
        )
    )
    return (
        row[0],
        row[1],
        row[2],
        row[3],
        content,
        row[5] == "needs_analysis",
        row[6] == "unknown",
        skills,
    )


def create_inference_job(
    connection: sqlite3.Connection,
    posting_ids: list[str],
    config: InferenceConfig | None = None,
    *,
    job_id: str | None = None,
) -> str:
    """Checkpoint selection; an explicit refresh may reuse its UUID as the job ID."""
    config = config or InferenceConfig()
    if (
        not posting_ids
        or len(posting_ids) > MAX_JOB_POSTINGS
        or len(set(posting_ids)) != len(posting_ids)
        or any(not posting_id.isdigit() for posting_id in posting_ids)
    ):
        raise InferenceError("Inference posting selection is invalid or unbounded.")
    if connection.in_transaction:
        raise InferenceError("Inference job creation requires its own transaction.")
    if job_id is not None:
        try:
            if str(UUID(job_id)) != job_id:
                raise ValueError
        except ValueError:
            raise InferenceError("Inference job identity is invalid.") from None
    job_id, created = job_id or str(uuid4()), _now()
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            items = []
            for posting_id in posting_ids:
                source = _source(connection, posting_id)
                _, input_hash = build_raw_prompt(
                    source[4].fields,
                    request_compensation=source[5],
                    request_skills=source[6],
                    deterministic_skills=source[7],
                )
                items.append((posting_id, source, input_hash))
            has_work = any(source[5] or source[6] for _, source, _ in items)
            connection.execute(
                """INSERT INTO inference_jobs
                   (job_id,status,model_id,endpoint,input_version,template_version,
                    validator_version,max_tokens,concurrency,max_attempts,
                    timeout_seconds,created_at,finished_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    job_id,
                    "pending" if has_work else "completed",
                    MODEL_ID,
                    ENDPOINT,
                    INPUT_VERSION,
                    TEMPLATE_VERSION,
                    VALIDATOR_VERSION,
                    MAX_TOKENS,
                    config.concurrency,
                    config.max_attempts,
                    TIMEOUT_SECONDS,
                    created,
                    None if has_work else created,
                ),
            )
            for ordinal, (posting_id, source, input_hash) in enumerate(items):
                needed = source[5] or source[6]
                connection.execute(
                    """INSERT INTO inference_job_items
                       (job_id,posting_id,ordinal,selected_listing_revision_id,
                        selected_content_revision_id,selected_listing_hash,
                        selected_content_hash,input_hash,request_compensation,
                        request_skills,status,finished_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        job_id,
                        posting_id,
                        ordinal,
                        source[0],
                        source[1],
                        source[2],
                        source[3],
                        input_hash,
                        int(source[5]),
                        int(source[6]),
                        "pending" if needed else "not_needed",
                        None if needed else created,
                    ),
                )
        return job_id
    except InferenceError:
        raise
    except Exception:
        raise InferenceError("Inference job creation failed.") from None


def inference_job_status(
    connection: sqlite3.Connection, job_id: str
) -> InferenceJobStatus:
    row = connection.execute(
        "SELECT status,stop_requested,error_category,lease_expires_at "
        "FROM inference_jobs WHERE job_id=?",
        (job_id,),
    ).fetchone()
    if row is None:
        raise InferenceError("Inference job does not exist.")
    persisted = cast(JobStatus, row[0])
    try:
        lease = datetime.fromisoformat(row[3]).astimezone(UTC) if row[3] else None
    except ValueError:
        lease = None
    effective: JobStatus = persisted
    if persisted in ("running", "stopping") and (
        lease is None or lease <= datetime.now(UTC)
    ):
        effective = "interrupted"
    counts = {
        row[0]: row[1]
        for row in connection.execute(
            "SELECT status,count(*) FROM inference_job_items "
            "WHERE job_id=? GROUP BY status",
            (job_id,),
        )
    }
    return InferenceJobStatus(
        job_id, persisted, effective, bool(row[1]), counts, row[2]
    )


def request_inference_stop(connection: sqlite3.Connection, job_id: str) -> None:
    """Stop new issuance; an already submitted local completion may still finish."""
    status = inference_job_status(connection, job_id)
    if status.effective_status in ("completed", "failed"):
        return
    now = _now()
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        if (
            status.effective_status == "interrupted"
            or status.persisted_status == "pending"
        ):
            connection.execute(
                "UPDATE inference_job_items SET status='interrupted',"
                "error_category='stopped',finished_at=? WHERE job_id=? "
                "AND status IN ('pending','running')",
                (now, job_id),
            )
            connection.execute(
                "UPDATE inference_jobs SET status='interrupted',stop_requested=1,"
                "finished_at=?,lease_expires_at=NULL,error_category='stopped' "
                "WHERE job_id=?",
                (now, job_id),
            )
        else:
            connection.execute(
                "UPDATE inference_jobs SET status='stopping',stop_requested=1 "
                "WHERE job_id=?",
                (job_id,),
            )


def _prepare_item(connection: sqlite3.Connection, row: sqlite3.Row) -> _Item:
    content = StoredPostingContent.model_validate_json(
        revision_snapshot(connection, "content", row["selected_content_revision_id"])
    )
    deterministic = tuple(
        result[0]
        for result in connection.execute(
            """SELECT catalog.canonical_name
               FROM posting_projection_current pointer
               JOIN normalized_skill_mentions mention
                 ON mention.projection_id=pointer.deterministic_projection_id
               JOIN normalized_skills catalog USING(skill_id)
               WHERE pointer.posting_id=? AND mention.excluded_reason IS NULL
               ORDER BY mention.ordinal""",
            (row["posting_id"],),
        )
    )
    prompt, input_hash = build_raw_prompt(
        content.fields,
        request_compensation=bool(row["request_compensation"]),
        request_skills=bool(row["request_skills"]),
        deterministic_skills=deterministic,
    )
    if input_hash != row["input_hash"]:
        raise InferenceError("Inference item input identity is inconsistent.")
    return _Item(
        row["job_id"],
        row["posting_id"],
        row["selected_content_hash"],
        row["input_hash"],
        content.fields,
        prompt,
        bool(row["request_compensation"]),
        bool(row["request_skills"]),
        deterministic,
    )


def _current_source(
    connection: sqlite3.Connection, posting_id: str, content_hash: str
) -> tuple[int, int, str, str] | None:
    row = connection.execute(
        """SELECT (SELECT max(revision_id) FROM posting_revisions
                    WHERE posting_id=postings.posting_id),
                  (SELECT max(revision_id) FROM posting_content_revisions
                    WHERE posting_id=postings.posting_id),
                  postings.content_hash,contents.content_hash
           FROM postings JOIN posting_contents contents USING(posting_id)
           WHERE postings.posting_id=?""",
        (posting_id,),
    ).fetchone()
    return None if row is None or row[3] != content_hash else tuple(row)


def _model_projection(
    connection: sqlite3.Connection,
    posting_id: str,
    source: tuple[int, int, str, str],
) -> tuple[int, bool]:
    version, now = _pipeline_version(), _now()
    inserted = connection.execute(
        """INSERT INTO analysis_projections
           (posting_id,listing_revision_id,content_revision_id,listing_hash,
            content_hash,producer_kind,producer_name,producer_version,
            parser_version,created_at)
           VALUES(?,?,?,?,?,'model',?,?,?,?)
           ON CONFLICT(posting_id,listing_revision_id,content_revision_id,
             producer_kind,producer_name,producer_version,parser_version) DO NOTHING""",
        (*((posting_id,) + source), MODEL_NAME, MODEL_VERSION, version, now),
    )
    row = connection.execute(
        """SELECT projection_id FROM analysis_projections WHERE posting_id=?
           AND listing_revision_id=? AND content_revision_id=?
           AND producer_kind='model' AND producer_name=? AND producer_version=?
           AND parser_version=?""",
        (posting_id, source[0], source[1], MODEL_NAME, MODEL_VERSION, version),
    ).fetchone()
    if row is None:
        raise InferenceError("Model projection identity was not retained.")
    return row[0], inserted.rowcount == 1


def _insert_result(
    connection: sqlite3.Connection,
    projection_id: int,
    result: ValidatedExtraction,
) -> None:
    for ordinal, rate in enumerate(result.rates):
        eligibility = rate.eligibility_evidence
        connection.execute(
            """INSERT INTO normalized_compensation_rates
               (projection_id,ordinal,bound_type,minimum_decimal,maximum_decimal,
                minimum_inclusive,maximum_inclusive,currency,period,
                hours_per_week_decimal,payout_frequency,eligibility,
                eligibility_field,eligibility_start,eligibility_end,source_field,
                source_start,source_end,source_hash)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                projection_id,
                ordinal,
                rate.bound_type,
                decimal_to_storage(rate.minimum) if rate.minimum is not None else None,
                decimal_to_storage(rate.maximum) if rate.maximum is not None else None,
                None if rate.minimum_inclusive is None else int(rate.minimum_inclusive),
                None if rate.maximum_inclusive is None else int(rate.maximum_inclusive),
                rate.currency,
                rate.period,
                None
                if rate.hours_per_week is None
                else decimal_to_storage(rate.hours_per_week),
                rate.payout_frequency,
                rate.eligibility,
                eligibility.field if eligibility else None,
                eligibility.start if eligibility else None,
                eligibility.end if eligibility else None,
                rate.evidence.field,
                rate.evidence.start,
                rate.evidence.end,
                rate.evidence.source_hash,
            ),
        )
    for ordinal, skill in enumerate(result.skills):
        connection.execute(
            "INSERT INTO normalized_skills(canonical_name) VALUES(?) "
            "ON CONFLICT(canonical_name) DO NOTHING",
            (skill.skill,),
        )
        skill_id = connection.execute(
            "SELECT skill_id FROM normalized_skills WHERE canonical_name=?",
            (skill.skill,),
        ).fetchone()[0]
        qualifier = skill.requirement_evidence
        connection.execute(
            """INSERT INTO normalized_skill_mentions
               (projection_id,skill_id,ordinal,requirement,source_field,
                source_start,source_end,requirement_start,requirement_end,source_hash)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                projection_id,
                skill_id,
                ordinal,
                skill.requirement,
                skill.evidence.field,
                skill.evidence.start,
                skill.evidence.end,
                qualifier.start if qualifier else None,
                qualifier.end if qualifier else None,
                skill.evidence.source_hash,
            ),
        )


def _copy_result(
    connection: sqlite3.Connection, source_projection: int, target_projection: int
) -> None:
    connection.execute(
        """INSERT INTO normalized_compensation_rates
           (projection_id,ordinal,bound_type,minimum_decimal,maximum_decimal,
            minimum_inclusive,maximum_inclusive,currency,period,
            hours_per_week_decimal,payout_frequency,eligibility,eligibility_field,
            eligibility_start,eligibility_end,source_field,source_start,source_end,
            source_hash)
           SELECT ?,ordinal,bound_type,minimum_decimal,maximum_decimal,
            minimum_inclusive,maximum_inclusive,currency,period,
            hours_per_week_decimal,payout_frequency,eligibility,eligibility_field,
            eligibility_start,eligibility_end,source_field,source_start,source_end,
            source_hash FROM normalized_compensation_rates WHERE projection_id=?""",
        (target_projection, source_projection),
    )
    connection.execute(
        """INSERT INTO normalized_skill_mentions
           (projection_id,skill_id,ordinal,requirement,excluded_reason,source_field,
            source_start,source_end,requirement_start,requirement_end,source_hash)
           SELECT ?,skill_id,ordinal,requirement,excluded_reason,source_field,
            source_start,source_end,requirement_start,requirement_end,source_hash
           FROM normalized_skill_mentions WHERE projection_id=?""",
        (target_projection, source_projection),
    )


def _cache(connection: sqlite3.Connection, row: sqlite3.Row) -> sqlite3.Row | None:
    result = connection.execute(
        """SELECT cache_id,projection_id FROM inference_cache WHERE input_hash=?
           AND content_hash=? AND model_id=? AND input_version=?
           AND template_version=? AND validator_version=? AND max_tokens=?""",
        (
            row["input_hash"],
            row["selected_content_hash"],
            MODEL_ID,
            INPUT_VERSION,
            TEMPLATE_VERSION,
            VALIDATOR_VERSION,
            MAX_TOKENS,
        ),
    ).fetchone()
    return cast(sqlite3.Row | None, result)


def _point_and_finish(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    projection_id: int,
    cache_id: int,
    cache_hit: bool,
) -> None:
    now = _now()
    connection.execute(
        """INSERT INTO posting_model_projection_current
           (posting_id,projection_id,input_hash,cache_id,updated_at)
           VALUES(?,?,?,?,?) ON CONFLICT(posting_id) DO UPDATE SET
             projection_id=excluded.projection_id,input_hash=excluded.input_hash,
             cache_id=excluded.cache_id,updated_at=excluded.updated_at""",
        (row["posting_id"], projection_id, row["input_hash"], cache_id, now),
    )
    connection.execute(
        """UPDATE inference_job_items SET status='accepted',cache_hit=?,cache_id=?,
               projection_id=?,finished_at=?,error_category=NULL
           WHERE job_id=? AND posting_id=?""",
        (cache_hit, cache_id, projection_id, now, row["job_id"], row["posting_id"]),
    )


def _accept(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    result: ValidatedExtraction | None,
    cached: sqlite3.Row | None,
) -> None:
    source = _current_source(
        connection, row["posting_id"], row["selected_content_hash"]
    )
    if source is None:
        connection.execute(
            "UPDATE inference_job_items SET status='rejected',"
            "error_category='stale_source',finished_at=? "
            "WHERE job_id=? AND posting_id=?",
            (_now(), row["job_id"], row["posting_id"]),
        )
        return
    projection_id, created = _model_projection(connection, row["posting_id"], source)
    if cached is not None:
        if created:
            _copy_result(connection, cached["projection_id"], projection_id)
        _point_and_finish(connection, row, projection_id, cached["cache_id"], True)
        return
    if result is None:
        raise InferenceError("Fresh model result is unavailable.")
    if created:
        _insert_result(connection, projection_id, result)
    now = _now()
    connection.execute(
        """INSERT INTO inference_cache
           (input_hash,content_hash,model_id,input_version,template_version,
            validator_version,max_tokens,projection_id,created_at)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            row["input_hash"],
            row["selected_content_hash"],
            MODEL_ID,
            INPUT_VERSION,
            TEMPLATE_VERSION,
            VALIDATOR_VERSION,
            MAX_TOKENS,
            projection_id,
            now,
        ),
    )
    cache_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
    _point_and_finish(connection, row, projection_id, cache_id, False)


def _checkpoint_accept(
    connection: sqlite3.Connection,
    job_id: str,
    row: sqlite3.Row,
    result: ValidatedExtraction,
    committed_lease: str,
) -> str:
    """Accept atomically, retaining the committed lease token across rollback."""
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            renewed_lease = _renew_lease(connection, job_id, committed_lease)
            _accept(connection, row, result, None)
    except _OwnershipLost:
        raise
    except Exception:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            renewed_lease = _renew_lease(connection, job_id, committed_lease)
            connection.execute(
                "UPDATE inference_job_items SET status='failed',"
                "error_category='internal',finished_at=? "
                "WHERE job_id=? AND posting_id=?",
                (_now(), job_id, row["posting_id"]),
            )
    return renewed_lease


def _finish(connection: sqlite3.Connection, job_id: str) -> None:
    statuses = {
        row[0]: row[1]
        for row in connection.execute(
            "SELECT status,count(*) FROM inference_job_items "
            "WHERE job_id=? GROUP BY status",
            (job_id,),
        )
    }
    stop = connection.execute(
        "SELECT stop_requested FROM inference_jobs WHERE job_id=?", (job_id,)
    ).fetchone()[0]
    if statuses.get("pending") or statuses.get("running"):
        return
    if stop or statuses.get("interrupted"):
        status, category = "interrupted", "stopped"
    elif statuses.get("failed"):
        status, category = "failed", "internal"
    else:
        status, category = "completed", None
    connection.execute(
        "UPDATE inference_jobs SET status=?,finished_at=?,lease_expires_at=NULL,"
        "error_category=? WHERE job_id=?",
        (status, _now(), category, job_id),
    )


def run_inference_job(
    connection: sqlite3.Connection,
    job_id: str,
    *,
    on_progress: Callable[[], None] | None = None,
) -> None:
    """Run pending items; report progress only after durable checkpoints commit."""
    row = connection.execute(
        "SELECT status,concurrency,max_attempts FROM inference_jobs WHERE job_id=?",
        (job_id,),
    ).fetchone()
    if row is None:
        raise InferenceError("Inference job does not exist.")
    if row[0] != "pending":
        raise InferenceError("Inference job requires explicit resume.")
    concurrency, max_attempts = row[1], row[2]
    lease = (datetime.now(UTC) + timedelta(seconds=LEASE_SECONDS)).isoformat(
        timespec="microseconds"
    )
    with connection:
        if (
            connection.execute(
                "UPDATE inference_jobs SET status='running',"
                "started_at=coalesce(started_at,?),lease_expires_at=? "
                "WHERE job_id=? AND status='pending' AND stop_requested=0",
                (_now(), lease, job_id),
            ).rowcount
            != 1
        ):
            raise InferenceError("Inference job could not be claimed.")
    if on_progress is not None:
        on_progress()
    try:
        _verify_model()
    except _ProviderError as error:
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                lease = _renew_lease(connection, job_id, lease)
                connection.execute(
                    "UPDATE inference_jobs SET status='failed',finished_at=?,"
                    "lease_expires_at=NULL,error_category=? WHERE job_id=?",
                    (_now(), error.category, job_id),
                )
        except _OwnershipLost:
            pass
        if on_progress is not None:
            on_progress()
        return
    while True:
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                lease = _renew_lease(connection, job_id, lease)
                job = connection.execute(
                    "SELECT stop_requested FROM inference_jobs WHERE job_id=?",
                    (job_id,),
                ).fetchone()
                if job[0]:
                    connection.execute(
                        "UPDATE inference_job_items SET status='interrupted',"
                        "error_category='stopped',finished_at=? WHERE job_id=? "
                        "AND status='pending'",
                        (_now(), job_id),
                    )
                    _finish(connection, job_id)
                    return
                rows = connection.execute(
                    "SELECT * FROM inference_job_items WHERE job_id=? "
                    "AND status='pending' ORDER BY ordinal LIMIT ?",
                    (job_id, concurrency),
                ).fetchall()
                if not rows:
                    _finish(connection, job_id)
                    return
                calls: list[tuple[sqlite3.Row, _Item]] = []
                for item_row in rows:
                    cached = _cache(connection, item_row)
                    if cached is not None:
                        _accept(connection, item_row, None, cached)
                        continue
                    item = _prepare_item(connection, item_row)
                    connection.execute(
                        "UPDATE inference_job_items SET status='running',"
                        "attempt_count=attempt_count+1,"
                        "started_at=coalesce(started_at,?) "
                        "WHERE job_id=? AND posting_id=?",
                        (_now(), job_id, item.posting_id),
                    )
                    calls.append((item_row, item))
        except _OwnershipLost:
            return
        if on_progress is not None:
            on_progress()
        if not calls:
            continue
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(_complete, item): (row, item) for row, item in calls}
            remaining = set(futures)
            while remaining:
                done, remaining = concurrent.futures.wait(
                    remaining,
                    timeout=LEASE_SECONDS / 3,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                if not done:
                    try:
                        with connection:
                            connection.execute("BEGIN IMMEDIATE")
                            lease = _renew_lease(connection, job_id, lease)
                    except _OwnershipLost:
                        return
                    if on_progress is not None:
                        on_progress()
                    continue
                for future in done:
                    item_row, item = futures[future]
                    try:
                        result = future.result()
                    except _ProviderError as error:
                        try:
                            with connection:
                                connection.execute("BEGIN IMMEDIATE")
                                lease = _renew_lease(connection, job_id, lease)
                                attempt = connection.execute(
                                    "SELECT attempt_count FROM inference_job_items "
                                    "WHERE job_id=? AND posting_id=?",
                                    (job_id, item.posting_id),
                                ).fetchone()[0]
                                retry = error.retryable and attempt < max_attempts
                                connection.execute(
                                    "UPDATE inference_job_items SET status=?,"
                                    "error_category=?,finished_at=? "
                                    "WHERE job_id=? AND posting_id=?",
                                    (
                                        "pending"
                                        if retry
                                        else "failed"
                                        if error.retryable
                                        else "rejected",
                                        error.category,
                                        None if retry else _now(),
                                        job_id,
                                        item.posting_id,
                                    ),
                                )
                        except _OwnershipLost:
                            return
                    else:
                        try:
                            lease = _checkpoint_accept(
                                connection, job_id, item_row, result, lease
                            )
                        except _OwnershipLost:
                            return
                    if on_progress is not None:
                        on_progress()


def _recovered_item_status(
    status: str, attempt_count: int, max_attempts: int
) -> Literal["pending", "failed"] | None:
    if status == "running" and attempt_count >= max_attempts:
        return "failed"
    if status in ("running", "interrupted", "failed") and attempt_count < max_attempts:
        return "pending"
    return None


def _prepare_resume(connection: sqlite3.Connection, job_id: str) -> bool:
    """Atomically recover only an expired/terminal runner and its abandoned items."""
    if connection.in_transaction:
        raise InferenceError("Inference resume requires its own transaction.")
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        job = connection.execute(
            "SELECT status,lease_expires_at,max_attempts FROM inference_jobs "
            "WHERE job_id=?",
            (job_id,),
        ).fetchone()
        if job is None:
            raise InferenceError("Inference job does not exist.")
        try:
            lease = datetime.fromisoformat(job[1]).astimezone(UTC) if job[1] else None
        except ValueError:
            lease = None
        if (
            job[0] in ("running", "stopping")
            and lease is not None
            and lease > datetime.now(UTC)
        ):
            raise InferenceError("Inference job is still active.")
        if job[0] not in ("running", "stopping", "interrupted", "failed"):
            raise InferenceError("Inference job is not resumable.")
        now = _now()
        for item in connection.execute(
            "SELECT posting_id,status,attempt_count FROM inference_job_items "
            "WHERE job_id=?",
            (job_id,),
        ):
            recovered = _recovered_item_status(item[1], item[2], job[2])
            if recovered == "failed":
                connection.execute(
                    "UPDATE inference_job_items SET status='failed',"
                    "error_category='internal',finished_at=? "
                    "WHERE job_id=? AND posting_id=?",
                    (now, job_id, item[0]),
                )
            elif recovered == "pending":
                connection.execute(
                    "UPDATE inference_job_items SET status='pending',"
                    "error_category=NULL,finished_at=NULL "
                    "WHERE job_id=? AND posting_id=?",
                    (job_id, item[0]),
                )
        pending = connection.execute(
            "SELECT count(*) FROM inference_job_items "
            "WHERE job_id=? AND status='pending'",
            (job_id,),
        ).fetchone()[0]
        if pending:
            connection.execute(
                "UPDATE inference_jobs SET status='pending',stop_requested=0,"
                "finished_at=NULL,lease_expires_at=NULL,error_category=NULL "
                "WHERE job_id=?",
                (job_id,),
            )
            return True
        connection.execute(
            "UPDATE inference_jobs SET status='failed',finished_at=?,"
            "lease_expires_at=NULL,error_category='internal' WHERE job_id=?",
            (now, job_id),
        )
        return False


def resume_inference_job(connection: sqlite3.Connection, job_id: str) -> None:
    """Explicitly reset retryable abandoned work, then run it."""
    if _prepare_resume(connection, job_id):
        run_inference_job(connection, job_id)


def start_inference_job(
    connection: sqlite3.Connection,
    posting_ids: list[str],
    config: InferenceConfig | None = None,
) -> str:
    job_id = create_inference_job(connection, posting_ids, config)
    if inference_job_status(connection, job_id).persisted_status == "pending":
        run_inference_job(connection, job_id)
    return job_id


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Explicit saved-posting Qwen extraction"
    )
    parser.add_argument("database", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start")
    selection = start.add_mutually_exclusive_group(required=True)
    selection.add_argument("--posting-id", action="append")
    selection.add_argument("--all", action="store_true", dest="all_postings")
    start.add_argument("--concurrency", type=int, default=4)
    start.add_argument("--max-attempts", type=int, default=2)
    for name in ("resume", "stop", "status"):
        commands.add_parser(name).add_argument("job_id")
    args = parser.parse_args()
    connection = open_database(args.database)
    try:
        if args.command == "start":
            posting_ids = (
                [
                    row[0]
                    for row in connection.execute(
                        "SELECT posting_id FROM postings ORDER BY posting_id LIMIT ?",
                        (MAX_JOB_POSTINGS + 1,),
                    )
                ]
                if args.all_postings
                else args.posting_id
            )
            job_id = create_inference_job(
                connection,
                posting_ids,
                InferenceConfig(
                    concurrency=args.concurrency, max_attempts=args.max_attempts
                ),
            )
            print(json.dumps({"job_id": job_id, "status": "pending"}), flush=True)
            run_inference_job(connection, job_id)
        else:
            job_id = args.job_id
            if args.command == "resume":
                resume_inference_job(connection, job_id)
            elif args.command == "stop":
                request_inference_stop(connection, job_id)
        status = inference_job_status(connection, job_id)
        print(json.dumps(status.__dict__, sort_keys=True, separators=(",", ":")))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
