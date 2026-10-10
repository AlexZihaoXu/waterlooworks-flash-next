"""Explicit bounded Pi-agent extraction for already saved postings."""

import argparse
import concurrent.futures
import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast
from uuid import UUID, uuid4

from pydantic import Field, ValidationError

from waterlooworks_flash_next.ai_custom import CustomServerError, validate_base_url
from waterlooworks_flash_next.ai_settings import AIConfig, AISettingsStore
from waterlooworks_flash_next.compact_history import revision_snapshot
from waterlooworks_flash_next.content import StoredPostingContent
from waterlooworks_flash_next.credentials import CredentialError
from waterlooworks_flash_next.database import open_database
from waterlooworks_flash_next.inference_prompt import (
    PiPrompt,
    build_pi_prompt,
    job_model_id,
    job_template_version,
)
from waterlooworks_flash_next.model_validation import (
    INPUT_VERSION,
    VALIDATOR_VERSION,
    ExtractionCandidate,
    ModelOutputError,
    ValidatedExtraction,
    validate_extraction,
)
from waterlooworks_flash_next.normalized_storage import decimal_to_storage
from waterlooworks_flash_next.openai_ledger import (
    AttemptLedger,
    BudgetLimits,
    BudgetStopped,
    daily_cost_stop_from_environment,
)
from waterlooworks_flash_next.parser_models import ParserModel
from waterlooworks_flash_next.pi_bridge import (
    ItemResult,
    PiItem,
    PiSession,
    SidecarError,
    require_sidecar,
)
from waterlooworks_flash_next.provider_lease import ProviderLease

# Heartbeat lease; renewed while items are in flight (each item has its own
# bounded per-turn and whole-item deadlines inside the sidecar).
LEASE_SECONDS = 180
MAX_JOB_POSTINGS = 10_000
MAX_BODY_BYTES = 4 * 1024 * 1024
# Known-$0 transient rejections (429/5xx) fail one item; this many in a row halt
# the job so a sustained outage cannot drain the queue.
TRANSIENT_HALT_THRESHOLD = 8
JobStatus = Literal[
    "pending", "running", "stopping", "completed", "failed", "interrupted"
]


class InferenceError(Exception):
    """Safe failure without source values or provider response text."""


class _OwnershipLost(Exception):
    """The persisted lease no longer belongs to this runner."""


class _ProviderError(Exception):
    def __init__(
        self, category: str, retryable: bool = False, halt: bool = False
    ) -> None:
        self.halt = halt
        super().__init__(category)
        self.category = category
        self.retryable = retryable


class InferenceConfig(ParserModel):
    concurrency: int | None = Field(default=None, ge=1, le=8)
    max_attempts: int = Field(default=1, ge=1, le=1)


@dataclass(frozen=True)
class JobIdentity:
    model_id: str
    endpoint: str
    template_version: str
    max_tokens: int

    @classmethod
    def from_config(cls, config: AIConfig) -> "JobIdentity":
        return cls(
            job_model_id(config),
            config.endpoint,
            job_template_version(config),
            config.max_output_tokens,
        )

    @property
    def producer_name(self) -> str:
        return self.model_id[:100]

    def as_tuple(self) -> tuple[str, str, str, str, str, int]:
        return (
            self.model_id,
            self.endpoint,
            INPUT_VERSION,
            self.template_version,
            VALIDATOR_VERSION,
            self.max_tokens,
        )


def active_config(path: Path) -> AIConfig:
    """The explicitly activated AI configuration; never the unsaved draft."""
    try:
        config = AISettingsStore(path).active()
    except Exception:
        config = None
    if config is None or config.model_id is None:
        raise InferenceError("AI extraction is not activated.")
    return config


def require_ready(path: Path) -> AIConfig:
    """Readiness without any model call: active config, credential, sidecar."""
    try:
        config = active_config(path)
    except InferenceError:
        raise CredentialError("AI extraction is not activated.") from None
    if config.access_mode != "custom" and not AISettingsStore(path).has_credential(
        config.access_mode
    ):
        raise CredentialError("AI access mode is not connected.")
    require_sidecar()
    return config


def run_limits(config: AIConfig) -> BudgetLimits:
    """API-key runs keep the USD stops; $0 modes are bounded by request count."""
    if config.is_zero_cost:
        return BudgetLimits(request_limit=config.request_limit_per_run)
    return BudgetLimits.from_environment()


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
    prompt: PiPrompt | None
    request_compensation: bool
    request_skills: bool
    deterministic_skills: tuple[str, ...]


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


def _pipeline_version(identity: JobIdentity) -> str:
    value = (
        f"{identity.model_id}|{identity.endpoint}|{INPUT_VERSION}|"
        f"{identity.template_version}|{VALIDATOR_VERSION}|{identity.max_tokens}|"
        f"{json.dumps(ExtractionCandidate.model_json_schema(), sort_keys=True)}"
    )
    return f"{VALIDATOR_VERSION}-{hashlib.sha256(value.encode()).hexdigest()[:12]}"


def _job_identity(connection: sqlite3.Connection, job_id: str) -> JobIdentity:
    row = connection.execute(
        "SELECT model_id,endpoint,template_version,max_tokens FROM inference_jobs "
        "WHERE job_id=?",
        (job_id,),
    ).fetchone()
    if row is None:
        raise InferenceError("Inference job does not exist.")
    return JobIdentity(row[0], row[1], row[2], row[3])


def _database_path(connection: sqlite3.Connection) -> Path:
    return Path(connection.execute("PRAGMA database_list").fetchone()[2])


def _validate_submission(
    item: _Item, payload: dict[str, object]
) -> ValidatedExtraction:
    candidate = ExtractionCandidate.model_validate(payload)
    return validate_extraction(
        candidate,
        item.fields,
        source_hash=item.content_hash,
        request_compensation=item.request_compensation,
        request_skills=item.request_skills,
        deterministic_skills=item.deterministic_skills,
    )


def _check(item: _Item, payload: dict[str, object]) -> tuple[bool, str | None]:
    """Authoritative in-loop check; hints are fixed text, never source values."""
    try:
        _validate_submission(item, payload)
        return True, None
    except ValidationError:
        return False, "Rejected: the payload does not match the required schema."
    except (ModelOutputError, ValueError):
        return False, (
            "Rejected: every evidence text must be copied exactly from the named "
            "section, categories must follow the request flags, and the pay and "
            "skill rules apply. Fix the evidence or return empty lists."
        )


# Sidecar per-item outcome -> (job category, retryable, halt). Caps and model
# behaviour fail one posting; credential, budget and ledger problems halt.
_ITEM_FAILURES: dict[str, tuple[str, bool, bool]] = {
    "auth": ("configuration", False, True),
    "budget_stopped": ("configuration", False, True),
    "credential_unavailable": ("configuration", False, True),
    "model_unavailable": ("model_unavailable", False, True),
    "reasoning_unsupported": ("configuration", False, True),
    "configuration": ("configuration", False, True),
    "sidecar_unavailable": ("configuration", False, True),
    "ledger": ("internal", False, True),
    "internal": ("internal", False, True),
    "protocol": ("internal", False, True),
    "rate_limited": ("http_client", True, False),
    "http_server": ("http_server", True, False),
    "http_client": ("http_client", False, True),
    "max_turns": ("response_schema", False, False),
    "max_tool_calls": ("response_schema", False, False),
    "no_submission": ("response_schema", False, False),
    "evidence_rejected": ("evidence_rejected", False, False),
}


def _failure(result: ItemResult, zero_cost: bool) -> "_ProviderError":
    category = result.category or "internal"
    if category in ("timeout", "connection"):
        # Unknown paid usage must halt; $0 modes treat it as one failed posting.
        return _ProviderError(category, retryable=zero_cost, halt=not zero_cost)
    mapped, retryable, halt = _ITEM_FAILURES.get(category, ("internal", False, True))
    return _ProviderError(mapped, retryable=retryable, halt=halt)


def _complete(item: _Item, session: PiSession) -> ValidatedExtraction:
    if item.prompt is None:
        raise _ProviderError("internal", halt=True)
    pi_item = PiItem(
        item_id=f"x-{item.posting_id}",
        kind="extraction",
        system=item.prompt.system,
        user=item.prompt.user,
        sections=item.prompt.sections,
        submit_schema=item.prompt.submit_schema,
        operation_key=f"{item.posting_id}|{item.input_hash}",
        check=lambda payload: _check(item, payload),
    )
    try:
        result = session.run_item(pi_item)
    except Exception:
        raise _ProviderError("internal", halt=True) from None
    if result.status != "submitted" or result.payload is None:
        raise _failure(result, session.config.is_zero_cost)
    try:
        return _validate_submission(item, result.payload)
    except (ModelOutputError, ValidationError, ValueError):
        raise _ProviderError("evidence_rejected") from None


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
    from waterlooworks_flash_next.inference_compatibility import (
        trusted_model_projection,
    )
    from waterlooworks_flash_next.source_enrichment import read_source_enrichment

    model = trusted_model_projection(connection, posting_id)
    reused = (False, False)
    if model is not None:
        reused = tuple(
            connection.execute(
                "SELECT coalesce(max(request_compensation),0),"
                "coalesce(max(request_skills),0) "
                "FROM inference_job_items WHERE cache_id=? AND status='accepted'",
                (model["cache_id"],),
            ).fetchone()
        )
    enrichment = read_source_enrichment(connection, posting_id).get(posting_id)
    salary_saved = enrichment is not None and enrichment.salary.state in (
        "accepted",
        "empty",
        "partial",
    )
    keywords_saved = enrichment is not None and enrichment.keywords_status.state in (
        "accepted",
        "empty",
        "partial",
    )
    return (
        row[0],
        row[1],
        row[2],
        row[3],
        content,
        row[5] == "needs_analysis" and not reused[0] and not salary_saved,
        row[6] == "unknown" and not reused[1] and not keywords_saved,
        skills,
    )


_Selection = tuple[
    str,
    tuple[int, int, str, str, StoredPostingContent, bool, bool, tuple[str, ...]],
    str,
]


def _select(
    connection: sqlite3.Connection, posting_id: str, ai: AIConfig
) -> _Selection:
    source = _source(connection, posting_id)
    _, input_hash = build_pi_prompt(
        source[4].fields,
        request_compensation=source[5],
        request_skills=source[6],
        deterministic_skills=source[7],
        config=ai,
    )
    return posting_id, source, input_hash


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
    ai = active_config(_database_path(connection))
    identity = JobIdentity.from_config(ai)
    try:
        # Expensive per-posting selection reads one WAL snapshot without the write
        # lock, so sign-in/settings/ledger writers are not blocked meanwhile.
        with connection:
            connection.execute("BEGIN")
            items = [_select(connection, posting_id, ai) for posting_id in posting_ids]
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            # Re-validate source identity under the write lock; re-select any
            # posting whose saved revision changed after the snapshot.
            items = [
                item
                if _current_source(connection, item[0], item[1][3])
                == tuple(item[1][:4])
                else _select(connection, item[0], ai)
                for item in items
            ]
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
                    *identity.as_tuple(),
                    config.concurrency or ai.parallel_streams,
                    config.max_attempts,
                    ai.request_timeout_seconds,
                    created,
                    None if has_work else created,
                ),
            )
            # Postings beyond the per-run cap are deferred (not_needed/stopped);
            # the next explicit run selects them again.
            budget = ai.postings_per_run
            for ordinal, (posting_id, source, input_hash) in enumerate(items):
                needed = source[5] or source[6]
                deferred = needed and budget <= 0
                budget -= int(needed)
                connection.execute(
                    """INSERT INTO inference_job_items
                       (job_id,posting_id,ordinal,selected_listing_revision_id,
                        selected_content_revision_id,selected_listing_hash,
                        selected_content_hash,input_hash,request_compensation,
                        request_skills,status,finished_at,error_category)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                        "pending" if needed and not deferred else "not_needed",
                        None if needed and not deferred else created,
                        "stopped" if deferred else None,
                    ),
                )
        AttemptLedger(_database_path(connection)).create_run(job_id, run_limits(ai))
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


def _job_config(connection: sqlite3.Connection, job_id: str) -> AIConfig:
    """Active configuration, required to be the one this job was created with."""
    ai = active_config(_database_path(connection))
    if JobIdentity.from_config(ai) != _job_identity(connection, job_id):
        raise InferenceError("Active AI configuration changed since job creation.")
    return ai


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
    prompt, input_hash = build_pi_prompt(
        content.fields,
        request_compensation=bool(row["request_compensation"]),
        request_skills=bool(row["request_skills"]),
        deterministic_skills=deterministic,
        config=_job_config(connection, row["job_id"]),
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
    job_id: str,
) -> tuple[int, bool]:
    identity = _job_identity(connection, job_id)
    version, now = _pipeline_version(identity), _now()
    name, producer_version = identity.producer_name, identity.template_version[:100]
    inserted = connection.execute(
        """INSERT INTO analysis_projections
           (posting_id,listing_revision_id,content_revision_id,listing_hash,
            content_hash,producer_kind,producer_name,producer_version,
            parser_version,created_at)
           VALUES(?,?,?,?,?,'model',?,?,?,?)
           ON CONFLICT(posting_id,listing_revision_id,content_revision_id,
             producer_kind,producer_name,producer_version,parser_version) DO NOTHING""",
        (*((posting_id,) + source), name, producer_version, version, now),
    )
    row = connection.execute(
        """SELECT projection_id FROM analysis_projections WHERE posting_id=?
           AND listing_revision_id=? AND content_revision_id=?
           AND producer_kind='model' AND producer_name=? AND producer_version=?
           AND parser_version=?""",
        (posting_id, source[0], source[1], name, producer_version, version),
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
    identity = _job_identity(connection, row["job_id"])
    result = connection.execute(
        """SELECT cache_id,projection_id FROM inference_cache WHERE input_hash=?
           AND content_hash=? AND model_id=? AND input_version=?
           AND template_version=? AND validator_version=? AND max_tokens=?""",
        (
            row["input_hash"],
            row["selected_content_hash"],
            identity.model_id,
            INPUT_VERSION,
            identity.template_version,
            VALIDATOR_VERSION,
            identity.max_tokens,
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
    projection_id, created = _model_projection(
        connection, row["posting_id"], source, row["job_id"]
    )
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
    identity = _job_identity(connection, row["job_id"])
    connection.execute(
        """INSERT INTO inference_cache
           (input_hash,content_hash,model_id,input_version,template_version,
            validator_version,max_tokens,projection_id,created_at)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            row["input_hash"],
            row["selected_content_hash"],
            identity.model_id,
            INPUT_VERSION,
            identity.template_version,
            VALIDATOR_VERSION,
            identity.max_tokens,
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
    provider_lease: ProviderLease | None = None,
) -> None:
    path = _database_path(connection)
    if provider_lease is not None:
        provider_lease.require_owner(path)
        _run_inference_job(connection, job_id, on_progress=on_progress)
    else:
        with ProviderLease(path):
            _run_inference_job(connection, job_id, on_progress=on_progress)


def _run_inference_job(
    connection: sqlite3.Connection,
    job_id: str,
    *,
    on_progress: Callable[[], None] | None = None,
) -> None:
    """Run pending items; report progress only after durable checkpoints commit."""
    row = connection.execute(
        "SELECT status FROM inference_jobs WHERE job_id=?",
        (job_id,),
    ).fetchone()
    if row is None:
        raise InferenceError("Inference job does not exist.")
    if row[0] != "pending":
        raise InferenceError("Inference job requires explicit resume.")
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
        path = _database_path(connection)
        ai = _job_config(connection, job_id)
        ledger = AttemptLedger(path)
        ledger.create_run(job_id, run_limits(ai))
        ledger.require_resumable(job_id)
        daily_cost_stop_from_environment()
        session = _open_session(path, ai, job_id)
    except (
        CredentialError,
        BudgetStopped,
        InferenceError,
        SidecarError,
        CustomServerError,
    ):
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                lease = _renew_lease(connection, job_id, lease)
                connection.execute(
                    "UPDATE inference_jobs SET status='failed',finished_at=?,"
                    "lease_expires_at=NULL,error_category=? WHERE job_id=?",
                    (_now(), "configuration", job_id),
                )
        except _OwnershipLost:
            pass
        if on_progress is not None:
            on_progress()
        return
    try:
        _drive(connection, job_id, lease, session, on_progress=on_progress)
    finally:
        session.close()


def _open_session(path: Path, ai: AIConfig, job_id: str) -> PiSession:
    """Readiness checks, then one sidecar for the whole run (no model call yet)."""
    require_ready(path)
    endpoint = (
        validate_base_url(ai.custom_base_url or "")
        if ai.access_mode == "custom"
        else None
    )
    return PiSession(path, ai, job_id, endpoint=endpoint).start()


def _claim(
    connection: sqlite3.Connection, job_id: str, lease: str, slots: int, batch: int
) -> tuple[str, list[tuple[sqlite3.Row, _Item]], bool]:
    """Claim up to `slots` uncached items in one short transaction.

    Cache hits among the next `batch` pending rows are accepted inline. Returns the
    renewed lease, the claimed calls and whether more pending work may remain.
    """
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        lease = _renew_lease(connection, job_id, lease)
        if connection.execute(
            "SELECT stop_requested FROM inference_jobs WHERE job_id=?", (job_id,)
        ).fetchone()[0]:
            return lease, [], False
        rows = connection.execute(
            "SELECT * FROM inference_job_items WHERE job_id=? "
            "AND status='pending' ORDER BY ordinal LIMIT ?",
            (job_id, batch),
        ).fetchall()
        calls: list[tuple[sqlite3.Row, _Item]] = []
        for item_row in rows:
            cached = _cache(connection, item_row)
            if cached is not None:
                _accept(connection, item_row, None, cached)
                continue
            if len(calls) >= slots:
                break
            item = _prepare_item(connection, item_row)
            connection.execute(
                "UPDATE inference_job_items SET status='running',"
                "attempt_count=attempt_count+1,"
                "started_at=coalesce(started_at,?) "
                "WHERE job_id=? AND posting_id=?",
                (_now(), job_id, item.posting_id),
            )
            calls.append((item_row, item))
        return lease, calls, bool(rows)


def _drive(
    connection: sqlite3.Connection,
    job_id: str,
    lease: str,
    provider: PiSession,
    *,
    on_progress: Callable[[], None] | None,
) -> None:
    """Keep up to `concurrency` calls in flight, claiming as each one finishes."""
    concurrency, max_attempts = connection.execute(
        "SELECT concurrency,max_attempts FROM inference_jobs WHERE job_id=?",
        (job_id,),
    ).fetchone()
    transient_streak = 0
    claiming = True
    in_flight: dict[
        concurrent.futures.Future[ValidatedExtraction], tuple[sqlite3.Row, _Item]
    ] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        while True:
            if claiming and len(in_flight) < concurrency:
                try:
                    lease, calls, claiming = _claim(
                        connection,
                        job_id,
                        lease,
                        concurrency - len(in_flight),
                        concurrency,
                    )
                except _OwnershipLost:
                    return
                for item_row, item in calls:
                    in_flight[pool.submit(_complete, item, provider)] = (
                        item_row,
                        item,
                    )
                if on_progress is not None:
                    on_progress()
                if claiming and len(in_flight) < concurrency:
                    continue
            if not in_flight:
                try:
                    with connection:
                        connection.execute("BEGIN IMMEDIATE")
                        lease = _renew_lease(connection, job_id, lease)
                        connection.execute(
                            "UPDATE inference_job_items SET status='interrupted',"
                            "error_category='stopped',finished_at=? WHERE job_id=? "
                            "AND status='pending' AND (SELECT stop_requested FROM "
                            "inference_jobs WHERE job_id=?)=1",
                            (_now(), job_id, job_id),
                        )
                        _finish(connection, job_id)
                except _OwnershipLost:
                    pass
                if on_progress is not None:
                    on_progress()
                return
            done, _ = concurrent.futures.wait(
                in_flight,
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
                item_row, item = in_flight.pop(future)
                try:
                    try:
                        result = future.result()
                    except _ProviderError:
                        raise
                    except Exception:
                        raise _ProviderError("internal", halt=True) from None
                except _ProviderError as error:
                    transient_streak = transient_streak + 1 if error.retryable else 0
                    if transient_streak >= TRANSIENT_HALT_THRESHOLD:
                        error.halt = True
                    try:
                        with connection:
                            connection.execute("BEGIN IMMEDIATE")
                            lease = _renew_lease(connection, job_id, lease)
                            attempt = connection.execute(
                                "SELECT attempt_count FROM inference_job_items "
                                "WHERE job_id=? AND posting_id=?",
                                (job_id, item.posting_id),
                            ).fetchone()[0]
                            retry = (
                                error.retryable
                                and not error.halt
                                and attempt < max_attempts
                            )
                            if error.halt:
                                connection.execute(
                                    "UPDATE inference_jobs "
                                    "SET stop_requested=1 WHERE job_id=?",
                                    (job_id,),
                                )
                            connection.execute(
                                "UPDATE inference_job_items SET status=?,"
                                "error_category=?,finished_at=? "
                                "WHERE job_id=? AND posting_id=?",
                                (
                                    "interrupted"
                                    if error.halt
                                    else "pending"
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
                    if retry:
                        claiming = True
                else:
                    transient_streak = 0
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
        identity = connection.execute(
            (
                "SELECT "
                "model_id,endpoint,input_version,template_version,valid"
                "ator_version,max_tokens FROM inference_jobs WHERE "
                "job_id=?"
            ),
            (job_id,),
        ).fetchone()
        try:
            current = JobIdentity.from_config(
                active_config(_database_path(connection))
            ).as_tuple()
        except InferenceError:
            current = None
        if tuple(identity) != current:
            raise InferenceError(
                "Historical inference configuration cannot resume with this provider."
            )
        try:
            AttemptLedger(_database_path(connection)).require_resumable(job_id)
        except BudgetStopped:
            raise InferenceError(
                "Unknown OpenAI usage requires review before an explicit new run."
            ) from None
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


def resume_inference_job(
    connection: sqlite3.Connection,
    job_id: str,
    *,
    provider_lease: ProviderLease | None = None,
) -> None:
    """Explicitly reset retryable abandoned work, then run it."""
    if _prepare_resume(connection, job_id):
        run_inference_job(connection, job_id, provider_lease=provider_lease)


def start_inference_job(
    connection: sqlite3.Connection,
    posting_ids: list[str],
    config: InferenceConfig | None = None,
) -> str:
    with ProviderLease(_database_path(connection)) as lease:
        job_id = create_inference_job(connection, posting_ids, config)
        if inference_job_status(connection, job_id).persisted_status == "pending":
            run_inference_job(connection, job_id, provider_lease=lease)
        return job_id


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Explicit saved-posting extraction with the active Pi AI "
        "configuration (bounded run; API-key mode may be paid)"
    )
    parser.add_argument("database", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start")
    selection = start.add_mutually_exclusive_group(required=True)
    selection.add_argument("--posting-id", action="append")
    selection.add_argument("--all", action="store_true", dest="all_postings")
    start.add_argument("--concurrency", type=int, default=None)
    start.add_argument("--max-attempts", type=int, default=1)
    for name in ("resume", "stop", "status"):
        commands.add_parser(name).add_argument("job_id")
    args = parser.parse_args()
    connection = open_database(args.database)
    lease = None
    try:
        if args.command in ("start", "resume"):
            lease = ProviderLease(args.database).acquire()
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
            run_inference_job(connection, job_id, provider_lease=lease)
        else:
            job_id = args.job_id
            if args.command == "resume":
                resume_inference_job(connection, job_id, provider_lease=lease)
            elif args.command == "stop":
                request_inference_stop(connection, job_id)
        status = inference_job_status(connection, job_id)
        print(json.dumps(status.__dict__, sort_keys=True, separators=(",", ":")))
    finally:
        if lease is not None:
            lease.close()
        connection.close()


if __name__ == "__main__":
    main()
