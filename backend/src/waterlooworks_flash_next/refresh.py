"""One explicit listings refresh at a time, with durable cooldown and safe events.

Offline operator recovery after a killed refresh (API stopped first):

    python -m waterlooworks_flash_next.refresh recover <database>
"""

import argparse
import json
import logging
import math
import sqlite3
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from waterlooworks_flash_next.api_models import (
    AdminRefreshStatus,
    RefreshLog,
    RefreshPacingStatus,
    RefreshSnapshot,
)
from waterlooworks_flash_next.auth_challenge import AuthChallenge, AuthChallengeStatus
from waterlooworks_flash_next.credentials import CredentialStore
from waterlooworks_flash_next.database import (
    StoredListing,
    open_database,
    save_listings,
)
from waterlooworks_flash_next.detail_capture import (
    capture_detail_source,
    install_capture_guard,
)
from waterlooworks_flash_next.detail_fetch import (
    ITEM_FAILURE_CATEGORIES,
    DetailCollection,
    DetailFetchError,
    DetailProgress,
    DetailSource,
    record_blocked_until,
)
from waterlooworks_flash_next.duo import at_waterlooworks, submit_and_observe_duo
from waterlooworks_flash_next.jobs import open_all_jobs
from waterlooworks_flash_next.listing_fields import LISTING_FIELDS as EXPORT_FIELDS
from waterlooworks_flash_next.listings import (
    CollectionInterrupted,
    ListingCollection,
    ListingFetchError,
)
from waterlooworks_flash_next.logging import stage_logger
from waterlooworks_flash_next.login import AuthDeadline, LoginSession, start_login
from waterlooworks_flash_next.provider_lease import ProviderBusy, ProviderLease
from waterlooworks_flash_next.refresh_budget import (
    describe_delay,
    pacing_status,
    record_pacing_stop,
    record_ww_request,
    refusal_message,
    rolling_requests,
)
from waterlooworks_flash_next.refresh_logging import (
    PAGE_FORMAT,
    safe_eta_seconds,
    safe_message,
)
from waterlooworks_flash_next.ww_pacing import (
    BROWSER_REQUESTS,
    PacingConfig,
    RequestBudgetExhausted,
    RequestGate,
    WaitKind,
    block_seconds,
)

# Browser loads before credential entry (sign-in landing page and the
# post-sign-in dashboard); the remaining BROWSER_REQUESTS are counted once the
# job board and its captured page-1 listing have loaded.
LOGIN_BROWSER_REQUESTS = 2
# Lifespan shutdown waits this long for the worker to reach a stop checkpoint;
# below compose stop_grace_period (190 s) so finalization precedes SIGKILL.
SHUTDOWN_JOIN_SECONDS = 150.0


class RefreshAnalysisError(Exception):
    """Saved collection survives an unsuccessful AI analysis phase."""


def configured_detail_limit(value: str | None) -> int | None:
    """Server-only cap for bounded verification; never accepted from a client."""
    if value is None:
        return None
    if (
        not value.isascii()
        or not value.isdigit()
        or len(value) > 6
        or not 1 <= int(value) <= 100_000
    ):
        raise ValueError("WW_DETAIL_LIMIT must be an integer from 1 to 100000.")
    return int(value)


_INTERRUPT_ATTEMPTS = tuple(
    f"UPDATE {table} SET status='interrupted',finished_at=?,"
    "error_category='cancelled' WHERE run_id=? AND status='running'"
    for table in ("posting_content_attempts", "posting_fetch_attempts")
)
UNCHANGED = "unchanged"
FAILED_SAME_REVISION = "failed_same_revision"
_ITEM_FAILURES = frozenset(category.value for category in ITEM_FAILURE_CATEGORIES)
# Latest terminal content attempt and the listing hash observed in that attempt's run.
_LATEST_ATTEMPT = (
    "SELECT a.status,a.error_category,r.content_hash "
    "FROM posting_content_attempts a "
    "LEFT JOIN posting_observations o ON o.posting_id=a.posting_id "
    "AND o.run_id=a.run_id "
    "LEFT JOIN posting_revisions r ON r.revision_id=o.revision_id "
    "WHERE a.posting_id=? AND a.status<>'running' "
    "ORDER BY a.rowid DESC LIMIT 1"
)
# Listing hash observed in the run of the latest successful content capture.
_LATEST_SUCCESS = (
    "SELECT r.content_hash FROM posting_content_attempts a "
    "JOIN posting_observations o ON o.posting_id=a.posting_id AND o.run_id=a.run_id "
    "JOIN posting_revisions r ON r.revision_id=o.revision_id "
    "WHERE a.posting_id=? AND a.status='succeeded' "
    "ORDER BY a.rowid DESC LIMIT 1"
)


def detail_skip_reason(
    current_hash: str,
    latest_attempt: tuple[str, str | None, str | None] | None,
    *,
    success_hash: str | None = None,
    content_current: bool = False,
    full_refetch: bool = False,
) -> str | None:
    """Why a listed posting needs no detail request this run, or None to fetch it.

    Saved content from the current extractor whose last successful capture saw
    the same listing hash is reused. A posting whose own response failed
    validation on the unchanged listing revision would fail identically again,
    so it waits for a listing change. ``full_refetch`` disables both skips.
    """
    if full_refetch:
        return None
    if content_current and success_hash is not None and success_hash == current_hash:
        return UNCHANGED
    if latest_attempt is not None:
        status, category, listing_hash = latest_attempt
        if (
            status == "failed"
            and category in _ITEM_FAILURES
            and listing_hash is not None
            and listing_hash == current_hash
        ):
            return FAILED_SAME_REVISION
    return None


def select_detail_queue(
    connection: sqlite3.Connection,
    listings: list[StoredListing],
    *,
    full_refetch: bool = False,
) -> tuple[list[StoredListing], dict[str, int]]:
    """Detail queue and counts of postings skipped per reason.

    Postings without any saved detail content come first (listing order within
    each group), so a run stopped by a request cap still makes progress on new
    postings and the next refresh continues where it stopped.
    """
    from waterlooworks_flash_next.content_storage import EXTRACTOR_VERSION

    queue: list[StoredListing] = []
    saved_ids: set[str] = set()
    skipped: dict[str, int] = {}
    for listing in listings:
        current = connection.execute(
            "SELECT content_hash FROM postings WHERE posting_id=?", (listing.id,)
        ).fetchone()
        reason = None
        if connection.execute(
            "SELECT 1 FROM posting_contents WHERE posting_id=?", (listing.id,)
        ).fetchone():
            saved_ids.add(listing.id)
        if current is not None and not full_refetch:
            latest = connection.execute(_LATEST_ATTEMPT, (listing.id,)).fetchone()
            success = connection.execute(_LATEST_SUCCESS, (listing.id,)).fetchone()
            content = connection.execute(
                "SELECT 1 FROM posting_contents "
                "WHERE posting_id=? AND extractor_version=?",
                (listing.id, EXTRACTOR_VERSION),
            ).fetchone()
            reason = detail_skip_reason(
                str(current[0]),
                tuple(latest) if latest is not None else None,
                success_hash=str(success[0]) if success is not None else None,
                content_current=content is not None,
            )
        if reason is None:
            queue.append(listing)
        else:
            skipped[reason] = skipped.get(reason, 0) + 1
    return prefer_unsaved(queue, saved_ids), skipped


def prefer_unsaved(
    queue: list[StoredListing], saved_ids: set[str]
) -> list[StoredListing]:
    """Stable order: postings without saved detail content first."""
    return [item for item in queue if item.id not in saved_ids] + [
        item for item in queue if item.id in saved_ids
    ]


def analysis_batches(ids: list[str]) -> list[list[str]]:
    """Split a selection into jobs within the inference job bound."""
    from waterlooworks_flash_next.inference import MAX_JOB_POSTINGS

    return [
        ids[start : start + MAX_JOB_POSTINGS]
        for start in range(0, len(ids), MAX_JOB_POSTINGS)
    ]


def configured_full_refetch(value: str | None) -> bool:
    """Server-only full detail refetch switch; never accepted from a client."""
    if value is None or value == "0":
        return False
    if value == "1":
        return True
    raise ValueError("WW_DETAIL_FULL_REFETCH must be 0 or 1.")


class RefreshConflict(Exception):
    def __init__(
        self, status_code: int, retry_after: int = 0, message: str | None = None
    ) -> None:
        self.status_code = status_code
        self.retry_after = retry_after
        super().__init__(
            message
            or (
                "Refresh already running."
                if status_code == 409
                else "Refresh cooldown is active."
            )
        )


def claim_refresh(path: Path, pacing: PacingConfig | None = None) -> str:
    """SQLite transaction prevents concurrent claims, including across API processes.

    Refused (429 with the remaining seconds) while a stored block, the rolling
    24-hour request budget or the cooldown since the previous run's finish is
    active; the longest wait wins. Stale running rows are NOT automatically
    cleared after a crash; the explicit offline ``recover`` command finalizes them.
    """
    pacing = pacing or PacingConfig()
    connection = open_database(path)
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM collection_runs WHERE status='running' LIMIT 1"
            ).fetchone():
                raise RefreshConflict(409)
            now = datetime.now(UTC)
            refusal = pacing_status(connection, pacing, now).refusal(now)
            if refusal is not None:
                reason, delay = refusal
                raise RefreshConflict(429, delay, refusal_message(reason, delay))
            run_id = str(uuid4())
            connection.execute(
                "INSERT INTO collection_runs (id, started_at) VALUES (?, ?)",
                (run_id, now.isoformat(timespec="microseconds")),
            )
            return run_id
    finally:
        connection.close()


def recover_interrupted_runs(path: Path) -> dict[str, int]:
    """Finalize running collection rows left by a killed process as interrupted.

    Holding the provider lease proves no live refresh/extraction owns the database
    (ProviderBusy otherwise). Never resumes, relogs in, fetches or infers.
    """
    with ProviderLease(path):
        connection = open_database(path)
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                now = datetime.now(UTC).isoformat(timespec="microseconds")
                runs = [
                    str(row[0])
                    for row in connection.execute(
                        "SELECT id FROM collection_runs WHERE status='running'"
                    )
                ]
                attempts = 0
                for run_id in runs:
                    for statement in _INTERRUPT_ATTEMPTS:
                        attempts += connection.execute(
                            statement, (now, run_id)
                        ).rowcount
                    connection.execute(
                        "UPDATE collection_runs SET status='interrupted',"
                        "finished_at=? WHERE id=? AND status='running'",
                        (now, run_id),
                    )
            return {"runs": len(runs), "attempts": attempts}
        finally:
            connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m waterlooworks_flash_next.refresh",
        description="Explicit offline refresh recovery; stop the API first.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    recover = commands.add_parser(
        "recover",
        help="Mark a stale running refresh and its open attempts interrupted.",
    )
    recover.add_argument("database", type=Path)
    args = parser.parse_args(argv)
    try:
        result = recover_interrupted_runs(args.database)
    except ProviderBusy:
        print(
            "Refresh recovery refused: the database is busy with a live refresh or "
            "extraction. Stop it first.",
            file=sys.stderr,
        )
        return 1
    except Exception:
        print("Refresh recovery failed; diagnostics suppressed.", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "attempts_interrupted": result["attempts"],
                "runs_interrupted": result["runs"],
            },
            sort_keys=True,
        )
    )
    return 0


class RefreshService:
    def __init__(
        self,
        path: Path,
        project_root: Path | None,
        *,
        detail_limit: int | None = None,
        full_refetch: bool = False,
        pacing: PacingConfig | None = None,
    ) -> None:
        self.path = path
        self.pacing = pacing or PacingConfig()
        self.project_root = project_root
        self.detail_limit = detail_limit
        self.full_refetch = full_refetch
        self.lock = threading.RLock()
        self.state = RefreshSnapshot()
        self.worker: threading.Thread | None = None
        self.cooldown_until: datetime | None = None
        self.provider_lease: ProviderLease | None = None
        self.auth_challenge = AuthChallenge()
        self.stop_event = threading.Event()

    def stop_requested(self) -> bool:
        return self.stop_event.is_set()

    def _checkpoint(self) -> None:
        if self.stop_event.is_set():
            raise CollectionInterrupted

    def request_stop(self, timeout: float | None = SHUTDOWN_JOIN_SECONDS) -> None:
        """Ask the worker to stop at its next checkpoint and wait a bounded time."""
        self.stop_event.set()
        worker = self.worker
        if worker is not None and worker.is_alive():
            worker.join(timeout)

    def restore(self) -> None:
        """Restore cooldown/unfinished state without automatically restarting."""
        connection = open_database(self.path)
        try:
            row = connection.execute(
                "SELECT id, status, pages_completed, records_received, "
                "unique_postings, duplicates, finished_at,details_completed,"
                "details_total,detail_requests,detail_retries,detail_limited,"
                "coalesce(p.ww_requests,0),p.pacing_stop,error_category "
                "FROM collection_runs LEFT JOIN collection_run_pacing p "
                "ON p.run_id=collection_runs.id "
                "ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if row:
                status = row[1]
                if status == "running" and self._lease_available():
                    # No live owner can hold this row: a killed process left it.
                    status = "interrupted"
                self.state = RefreshSnapshot.model_validate(
                    {
                        "run_id": row[0],
                        "status": status,
                        "stage": "recovery_required"
                        if row[1] == "running"
                        else "previous_run",
                        "pages_completed": row[2],
                        "records_received": row[3],
                        "unique_postings": row[4],
                        "duplicates": row[5],
                        "details_completed": row[7],
                        "details_total": row[8],
                        "detail_requests": row[9],
                        "detail_retries": row[10],
                        "detail_limited": bool(row[11]),
                        "ww_requests": row[12],
                        "pacing_stop": row[13],
                        "error_category": row[14],
                    }
                )
            self.cooldown_until = pacing_status(connection, self.pacing).next_allowed_at
            if (
                row
                and connection.execute(
                    "SELECT 1 FROM inference_jobs WHERE job_id=?", (row[0],)
                ).fetchone()
            ):
                self._analysis_progress(connection, row[0], log=False)
        finally:
            connection.close()

    def _lease_available(self) -> bool:
        try:
            with ProviderLease(self.path):
                return True
        except ProviderBusy:
            return False

    def snapshot(self) -> RefreshSnapshot:
        with self.lock:
            snapshot = self.state.model_copy(deep=True)
            if self.cooldown_until:
                snapshot.retry_after = max(
                    0,
                    math.ceil(
                        (self.cooldown_until - datetime.now(UTC)).total_seconds()
                    ),
                )
            return snapshot

    def challenge_status(self) -> AuthChallengeStatus:
        with self.lock:
            return self.auth_challenge.snapshot(
                self.state.run_id if self.state.status == "running" else None
            )

    def pacing_status(self) -> RefreshPacingStatus:
        """Configured pacing and rolling 24-hour usage, read from SQLite."""
        from waterlooworks_flash_next import ww_pacing

        connection = open_database(self.path)
        try:
            status = pacing_status(connection, self.pacing)
            last = connection.execute(
                "SELECT p.pacing_stop,r.error_category FROM collection_runs r "
                "LEFT JOIN collection_run_pacing p ON p.run_id=r.id "
                "WHERE r.status<>'running' ORDER BY r.finished_at DESC LIMIT 1"
            ).fetchone()
        finally:
            connection.close()
        next_allowed = status.next_allowed_at
        return RefreshPacingStatus(
            request_cap=self.pacing.request_cap,
            daily_request_cap=self.pacing.daily_request_cap,
            min_interval_minutes=self.pacing.min_interval_minutes,
            min_gap_seconds=ww_pacing.MIN_GAP_SECONDS,
            max_gap_seconds=ww_pacing.MAX_GAP_SECONDS,
            rest_every_min=ww_pacing.REST_EVERY_MIN,
            rest_every_max=ww_pacing.REST_EVERY_MAX,
            rest_min_seconds=ww_pacing.REST_MIN_SECONDS,
            rest_max_seconds=ww_pacing.REST_MAX_SECONDS,
            used_24h=status.used,
            remaining_24h=status.remaining,
            next_allowed_at=next_allowed.isoformat() if next_allowed else None,
            blocked_until=status.blocked_until.isoformat()
            if status.blocked_until
            else None,
            last_pacing_stop=last[0] if last else None,
            last_error_category=last[1] if last else None,
        )

    def admin_status(self) -> AdminRefreshStatus:
        challenge = self.challenge_status()
        return AdminRefreshStatus(**challenge.model_dump(), pacing=self.pacing_status())

    def emit(self, message: str, level: str = "info") -> None:
        with self.lock:
            sequence = self.state.logs[-1].sequence + 1 if self.state.logs else 1
            self.state.logs.append(
                RefreshLog(
                    sequence=sequence,
                    at=datetime.now(UTC).isoformat(),
                    level="critical"
                    if level == "critical"
                    else "error"
                    if level == "error"
                    else "warning"
                    if level == "warning"
                    else "debug"
                    if level == "debug"
                    else "info",
                    message=message,
                )
            )
            self.state.logs = self.state.logs[-200:]

    def start(self) -> str:
        with self.lock:
            if self.project_root is None:
                raise RuntimeError("Manual refresh is disabled.")
            CredentialStore(self.path).require_ready()
            from waterlooworks_flash_next.inference import require_ready, run_limits

            # Active Pi AI configuration, credential and sidecar; no model call.
            run_limits(require_ready(self.path))
            lease = ProviderLease(self.path)
            try:
                lease.acquire()
            except ProviderBusy:
                raise RefreshConflict(409) from None
            try:
                run_id = claim_refresh(self.path, self.pacing)
            except Exception:
                lease.close()
                raise
            self.provider_lease = lease
            self.stop_event.clear()
            self.state = RefreshSnapshot(
                run_id=run_id, status="running", stage="authentication"
            )
            self.emit(
                "Fresh authentication starting. If approval is required, "
                "the admin can open Admin settings from the account button."
            )
            self.worker = threading.Thread(
                target=self._run, args=(run_id,), name="listing-refresh", daemon=False
            )
            try:
                self.worker.start()
            except Exception:
                try:
                    self._finish(run_id, False, "internal")
                finally:
                    lease.close()
                    self.provider_lease = None
                raise RuntimeError("Could not start refresh worker.") from None
            return run_id

    def persist_listing_block(self, run_id: str, error: ListingFetchError) -> None:
        """Persist a listing-phase ban-risk block exactly like the detail phase."""
        blocked = block_seconds(error)
        if blocked is None:
            return
        try:
            connection = open_database(self.path)
            try:
                record_blocked_until(connection, run_id, blocked)
            finally:
                connection.close()
        except Exception:
            self.emit(
                "Could not persist the refresh block; diagnostics suppressed.",
                "warning",
            )

    def _finish(
        self,
        run_id: str,
        success: bool,
        category: str | None,
        *,
        interrupted: bool = False,
    ) -> None:
        status: Literal["completed", "failed", "interrupted"] = (
            "completed" if success else "interrupted" if interrupted else "failed"
        )
        now = datetime.now(UTC)
        connection = open_database(self.path)
        try:
            with connection:
                connection.execute(
                    "UPDATE collection_runs SET status=?, finished_at=?, "
                    "pages_completed=?, records_received=?, unique_postings=?, "
                    "duplicates=?, error_category=? WHERE id=?",
                    (
                        status,
                        now.isoformat(timespec="microseconds"),
                        self.state.pages_completed,
                        self.state.records_received,
                        self.state.unique_postings,
                        self.state.duplicates,
                        category,
                        run_id,
                    ),
                )
                record_pacing_stop(connection, run_id, self.state.pacing_stop)
            next_allowed = pacing_status(connection, self.pacing, now).next_allowed_at
        finally:
            connection.close()
        with self.lock:
            self.state.status = status
            self.state.stage = "complete" if success else "stopped"
            self.state.eta_seconds = None
            self.state.detail_wait_seconds = 0
            self.state.error_category = category
            self.cooldown_until = next_allowed
        self.emit(
            (
                "Refresh finished partially at a WaterlooWorks request cap; "
                "saved progress is kept and the remaining postings wait for "
                "the next refresh."
                if self.state.pacing_stop in ("request_cap", "daily_cap")
                else "Refresh completed with the server detail cap; "
                "remaining details were not requested."
                if self.state.detail_limited
                else "Collection and AI extraction completed."
                if self.state.analysis_job_id is not None
                else "Listing and detail-content refresh completed."
            )
            if success
            else "Refresh interrupted by shutdown; completed checkpoints are "
            "preserved. No automatic restart."
            if interrupted
            else "Refresh stopped; completed checkpoints are preserved. "
            "No automatic restart.",
            "info" if success else "error",
        )

    def _analysis_progress(
        self,
        connection: sqlite3.Connection,
        job_id: str,
        *,
        log: bool = True,
        total: int | None = None,
    ) -> None:
        from waterlooworks_flash_next.inference import inference_job_status

        status = inference_job_status(connection, job_id)
        counts = status.counts
        if total is None:
            # A refresh job reuses its run ID: its scope is that run's listing.
            total = connection.execute(
                "SELECT count(*) FROM posting_observations WHERE run_id=?", (job_id,)
            ).fetchone()[0]
        missing = max(0, total - sum(counts.values()))
        cache_hits = connection.execute(
            "SELECT count(*) FROM inference_job_items WHERE job_id=? AND cache_hit=1",
            (job_id,),
        ).fetchone()[0]
        accepted = counts.get("accepted", 0)
        rejected = counts.get("rejected", 0)
        failed = counts.get("failed", 0) + counts.get("interrupted", 0)
        skipped = counts.get("not_needed", 0) + missing
        completed = accepted + rejected + failed + skipped
        with self.lock:
            previous = (self.state.analysis_completed, self.state.analysis_cache_hits)
            self.state.analysis_job_id = job_id
            self.state.analysis_total = total
            self.state.analysis_completed = completed
            self.state.analysis_accepted = accepted
            self.state.analysis_rejected = rejected
            self.state.analysis_failed = failed
            self.state.analysis_skipped = skipped
            self.state.analysis_cache_hits = cache_hits
        if log and previous != (completed, cache_hits):
            self.emit(
                f"AI extraction: {completed}/{total} processed; "
                f"{accepted} accepted, {rejected} rejected, {failed} failed, "
                f"{skipped} skipped; {cache_hits} cache hits."
            )

    def _analyze_saved_data(self, run_id: str) -> None:
        from waterlooworks_flash_next.normalized_storage import normalize_saved_postings

        with self.lock:
            self.state.stage = "normalizing"
            self.state.eta_seconds = None
            self.state.detail_wait_seconds = 0
        self.emit("Collection saved. Normalizing all saved postings locally.")
        connection = open_database(self.path)
        try:
            normalized = normalize_saved_postings(connection)
            self.emit(
                f"Normalization: {normalized.postings_seen} postings; "
                f"{normalized.projections_created} updated, "
                f"{normalized.projections_reused} reused."
            )
            # Only postings in this run's listing pass; history never grows a job.
            ids = [
                str(row[0])
                for row in connection.execute(
                    "SELECT c.posting_id FROM posting_contents c "
                    "JOIN posting_observations o ON o.posting_id=c.posting_id "
                    "WHERE o.run_id=? ORDER BY c.posting_id",
                    (run_id,),
                )
            ]
            total = connection.execute(
                "SELECT count(*) FROM posting_observations WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            if not ids:
                with self.lock:
                    self.state.analysis_total = total
                    self.state.analysis_completed = total
                    self.state.analysis_skipped = total
                self.emit("No saved detail content to analyze; no model request made.")
                return
            batches = analysis_batches(ids)
            rejected = False
            for index, batch in enumerate(batches):
                self._checkpoint()
                rejected |= self._analyze_batch(
                    connection,
                    batch,
                    # The first job reuses the run ID for durable status/recovery.
                    job_id=run_id if index == 0 else None,
                    total=total if len(batches) == 1 else len(batch),
                    label=f" (batch {index + 1}/{len(batches)})"
                    if len(batches) > 1
                    else "",
                    skipped=total - len(ids) if index == 0 else 0,
                )
            self.emit(
                "AI extraction completed. Rejected or unavailable facts remain "
                "unknown; no model reasoning or posting content was logged.",
                "warning" if rejected else "info",
            )
        except (RefreshAnalysisError, CollectionInterrupted):
            raise
        except Exception:
            self.emit(
                "AI analysis failed; saved collection is preserved. "
                "Diagnostics suppressed.",
                "error",
            )
            raise RefreshAnalysisError("AI analysis failed.") from None
        finally:
            connection.close()

    def _analyze_batch(
        self,
        connection: sqlite3.Connection,
        ids: list[str],
        *,
        job_id: str | None,
        total: int,
        label: str,
        skipped: int,
    ) -> bool:
        """Run one bounded extraction job; return whether any item was rejected."""
        from waterlooworks_flash_next.inference import (
            InferenceConfig,
            active_config,
            create_inference_job,
            inference_job_status,
            request_inference_stop,
            run_inference_job,
        )
        from waterlooworks_flash_next.inference_schema import _ERROR_CATEGORIES

        ai = active_config(self.path)
        job_id = create_inference_job(connection, ids, InferenceConfig(), job_id=job_id)
        with self.lock:
            self.state.stage = "analysis"
            self.state.analysis_total = total
            self.state.analysis_job_id = job_id
            self.state.analysis_provider = ai.label()
        self.emit(
            f"Extracting with {ai.label()} (job {job_id}{label}): {len(ids)} saved "
            f"postings queued; {skipped} without saved details skipped; "
            f"at most {ai.parallel_streams} parallel streams; validated cache "
            "reused."
        )
        stopping = False

        def progress() -> None:
            nonlocal stopping
            self._analysis_progress(connection, job_id, total=total)
            if self.stop_event.is_set() and not stopping:
                # Durable stop flag: no new batch is claimed; in-flight
                # completions may still checkpoint.
                stopping = True
                request_inference_stop(connection, job_id)

        progress()
        if inference_job_status(connection, job_id).persisted_status == "pending":
            run_inference_job(
                connection,
                job_id,
                on_progress=progress,
                provider_lease=self.provider_lease,
            )
        progress()
        self._checkpoint()
        status = inference_job_status(connection, job_id)
        for reason, count in connection.execute(
            "SELECT error_category,count(*) FROM inference_job_items "
            "WHERE job_id=? AND error_category IS NOT NULL GROUP BY error_category",
            (job_id,),
        ):
            if reason in _ERROR_CATEGORIES:
                self.emit(
                    f"AI extraction outcome: {reason}; {count} postings.",
                    "warning",
                )
        if status.error_category in _ERROR_CATEGORIES:
            self.emit(
                f"AI extraction failure category: {status.error_category}.",
                "error",
            )
        if status.persisted_status != "completed":
            self.emit(
                "AI extraction did not complete. Saved collection and accepted "
                "checkpoints are preserved. Check AI usage, budget "
                "and the saved job "
                "status; no automatic resume.",
                "error",
            )
            raise RefreshAnalysisError("AI analysis did not complete.")
        return bool(status.counts.get("rejected"))

    def _save_partial_listings(
        self, run_id: str, collection: ListingCollection
    ) -> None:
        """Persist pages fetched before a listing failure; never raises."""
        try:
            listings = _stored_listings(collection)
            connection = open_database(self.path)
            try:
                save_listings(
                    connection, run_id, listings, received_at=collection.received_at
                )
            finally:
                connection.close()
        except Exception:
            self.emit(
                "Partial listing pass could not be saved; diagnostics suppressed.",
                "error",
            )
            return
        self.emit(
            f"Partial listing pass saved: {collection.pages_completed} pages, "
            f"{len(listings)} unique postings. No deletion is inferred; details "
            "and analysis were not run."
        )

    def _detail_progress(self, progress: DetailProgress) -> None:
        with self.lock:
            self.state.stage = progress.stage
            self.state.details_completed = progress.completed
            self.state.details_total = progress.total
            self.state.detail_requests = progress.requests
            self.state.detail_retries = progress.retries
            self.state.detail_bursts = progress.rests
            self.state.detail_wait_seconds = round(progress.wait_seconds, 3)
            self.state.eta_seconds = (
                None  # No borrowed listing ETA or invented throughput.
            )
        if progress.stage == "detail_retry":
            self.emit(
                f"Detail retry scheduled; waiting {progress.wait_seconds:.1f}s.",
                "warning",
            )
        elif progress.stage == "detail_rest":
            self.emit(_rest_message(progress.wait_seconds))
        else:
            self.emit(
                f"Detail progress: {progress.completed}/{progress.total} saved; "
                f"{progress.failed} failed validation; "
                f"{progress.requests} requests; {progress.retries} retries; "
                f"{progress.rests} rest breaks."
            )

    def _listing_wait(self, kind: WaitKind | None, seconds: float) -> None:
        with self.lock:
            self.state.stage = "listings" if kind is None else f"listing_{kind}"
            self.state.detail_wait_seconds = round(seconds, 3)
            if kind is not None:
                self.state.eta_seconds = None
        if kind == "rest":
            self.emit(_rest_message(seconds))
        elif kind == "retry":
            self.emit(f"Listing retry scheduled; waiting {seconds:.1f}s.", "warning")

    def _record_request(self, connection: sqlite3.Connection, run_id: str) -> None:
        """Durably count one WaterlooWorks request before it is sent."""
        record_ww_request(connection, run_id)
        with self.lock:
            self.state.ww_requests += 1

    def _sleep(self, seconds: float) -> None:
        # Wakes early on a shutdown request; the gate then stops before sending.
        self.stop_event.wait(seconds)

    def _new_gate(self, connection: sqlite3.Connection, run_id: str) -> RequestGate:
        used = rolling_requests(connection, datetime.now(UTC), exclude_run=run_id)
        gate = RequestGate(
            self.pacing,
            daily_remaining=self.pacing.daily_request_cap - used,
            record_request=lambda: self._record_request(connection, run_id),
            report_wait=self._listing_wait,
            sleep=self._sleep,
            should_stop=self.stop_requested,
        )
        with self.lock:
            self.state.ww_request_budget = gate.budget
        return gate

    def _pacing_stopped(self, stop: RequestBudgetExhausted, left: str) -> None:
        with self.lock:
            self.state.pacing_stop = stop.reason
        self.emit(
            (
                "Per-run WaterlooWorks request cap reached"
                if stop.reason == "request_cap"
                else "Rolling 24-hour WaterlooWorks request budget reached"
            )
            + f"; stopped cleanly. {left} left for the next refresh; saved "
            "progress is kept.",
            "warning",
        )

    def _ban_risk(self, error: ListingFetchError) -> None:
        blocked = block_seconds(error)
        if blocked is None:
            return
        with self.lock:
            self.state.pacing_stop = "ban_risk"
        self.emit(
            f"BAN RISK: WaterlooWorks returned a warning sign ({error.category.value}; "
            f"HTTP {error.status if error.status is not None else 'unavailable'}). "
            f"The run stopped at once without retrying; refreshes are blocked for "
            f"{describe_delay(blocked)}. Check the account in a browser before "
            "clearing the block with the rate_limit clear command.",
            "critical",
        )

    def _run(self, run_id: str) -> None:
        session: LoginSession | None = None
        success = False
        interrupted = False
        category: str | None = "internal"
        handler = SafeRefreshHandler(self)
        scopes = [stage_logger(scope) for scope in ("login", "duo", "jobs", "listings")]
        for logger in scopes:
            logger.addHandler(handler)
        # Dedicated connection: request accounting commits outside phase writes.
        accounting: sqlite3.Connection | None = None
        try:
            assert self.project_root is not None
            accounting = open_database(self.path)
            gate = self._new_gate(accounting, run_id)
            deadline = AuthDeadline()
            self.auth_challenge.begin(run_id, deadline)
            # Exactly one sign-in attempt per run: a failure ends the run.
            gate.note_external(time.monotonic(), LOGIN_BROWSER_REQUESTS)
            session = start_login(
                self.path,
                project_root=self.project_root,
                block_service_workers=True,
                deadline=deadline,
            )
            submit_and_observe_duo(
                session,
                lambda code: self.auth_challenge.publish(run_id, code),
                lambda: self.auth_challenge.clear(run_id),
            )
            if not at_waterlooworks(session.page):
                raise RuntimeError("Authentication not confirmed.")
            self._checkpoint()
            source = open_all_jobs(session.page)
            if source is None:
                raise RuntimeError("No fresh listing source.")
            gate.note_external(
                source.finished_at, BROWSER_REQUESTS - LOGIN_BROWSER_REQUESTS
            )
            source.gate = gate
            # Suppress browser background egress while direct listings run.
            install_capture_guard(session.page, "")
            with self.lock:
                self.state.stage = "listings"
                self.state.total_pages = max(
                    1, math.ceil(source.first_page.totalResults / source.page_size)
                )
            collection = ListingCollection(source, should_stop=self.stop_requested)
            try:
                collection.run()  # Captured page 1 reused; shared paced gate.
            except (ListingFetchError, CollectionInterrupted):
                # Upsert-only and never infers deletion: partial pages are safe.
                self._save_partial_listings(run_id, collection)
                raise
            except RequestBudgetExhausted as stop:
                # Clean partial finish: pages so far are saved, no details run.
                self._save_partial_listings(run_id, collection)
                self._pacing_stopped(
                    stop, "Remaining listing pages and all detail requests are"
                )
                success, category = True, None
                return
            with self.lock:
                self.state.stage = "saving"
                self.state.eta_seconds = None
            self.emit("Saving the completed listing pass and its revision history.")
            listings = _stored_listings(collection)
            connection = open_database(self.path)
            try:
                save_listings(
                    connection, run_id, listings, received_at=collection.received_at
                )
                eligible, skipped = select_detail_queue(
                    connection, listings, full_refetch=self.full_refetch
                )
                queue = (
                    eligible
                    if self.detail_limit is None
                    else eligible[: self.detail_limit]
                )
                limited = len(queue) < len(eligible)
                with connection:
                    connection.execute(
                        "UPDATE collection_runs SET pages_completed=?,"
                        "records_received=?,"
                        "unique_postings=?,duplicates=?,details_total=?,"
                        "detail_limited=? "
                        "WHERE id=?",
                        (
                            collection.pages_completed,
                            collection.records_received,
                            len(listings),
                            collection.duplicates,
                            len(queue),
                            int(limited),
                            run_id,
                        ),
                    )
                with self.lock:
                    self.state.stage = "details"
                    self.state.details_total = len(queue)
                    self.state.detail_limited = limited
                    self.state.eta_seconds = None
                self.emit(
                    "Full listing pass saved. "
                    "Starting sequential detail-content collection."
                )
                if self.full_refetch:
                    self.emit(
                        "Full detail refetch enabled by the server; "
                        "unchanged postings are requested again."
                    )
                elif skipped.get(UNCHANGED):
                    self.emit(
                        f"{skipped[UNCHANGED]} postings skipped: listing unchanged "
                        "since their last successful detail capture."
                    )
                if skipped.get(FAILED_SAME_REVISION):
                    self.emit(
                        f"{skipped[FAILED_SAME_REVISION]} postings skipped: their "
                        "detail content failed validation on the same listing "
                        "revision.",
                        "warning",
                    )

                def bootstrap(
                    posting: StoredListing, request: Callable[[Callable[[], str]], str]
                ) -> DetailSource:
                    nonlocal session
                    assert session is not None
                    try:
                        detail_source = capture_detail_source(
                            session.page, source, posting, request, self.emit
                        )
                    finally:
                        # Cleanup must not replace a fatal bootstrap status (429,
                        # auth, etc.) needed by the durable attempt checkpoint.
                        failing = sys.exception() is not None
                        try:
                            session.close()
                        except Exception:
                            if not failing:
                                raise
                            self.emit(
                                "Browser cleanup reported a warning; "
                                "diagnostics suppressed.",
                                "warning",
                            )
                        else:
                            session = None
                    detail_source.confirm_capture()
                    return detail_source

                details = DetailCollection(
                    None,
                    connection,
                    run_id,
                    queue,
                    self._detail_progress,
                    bootstrap=bootstrap,
                    should_stop=self.stop_requested,
                    gate=gate,
                )
                try:
                    details.run()
                except RequestBudgetExhausted as stop:
                    # Clean partial finish; saved details and analysis continue.
                    self._pacing_stopped(
                        stop,
                        f"{len(queue) - details.completed - details.failed} "
                        "postings are",
                    )
                if details.failed:
                    self.emit(
                        f"{details.failed} postings failed detail-content "
                        "validation and were skipped; they are retried after "
                        "their listing changes.",
                        "warning",
                    )
            finally:
                connection.close()
            if session is not None:
                session.close()
                session = None
            self._checkpoint()
            self._analyze_saved_data(run_id)
            success, category = True, None
        except CollectionInterrupted:
            interrupted, category = True, None
        except RefreshAnalysisError:
            category = "internal"
        except ListingFetchError as error:
            category = error.category.value
            if not isinstance(error, DetailFetchError):
                # Detail failures persist their block at the failing checkpoint.
                self.persist_listing_block(run_id, error)
            self.emit(
                f"Refresh failure category: {category}; "
                f"HTTP {error.status if error.status is not None else 'unavailable'}.",
                "error",
            )
            self._ban_risk(error)
        except (Exception, SystemExit):
            # Safe terminal failure; never relogin or repeat an MFA push.
            category = "internal"
        finally:
            self.auth_challenge.clear(run_id)
            if session:
                try:
                    session.close()
                except Exception:
                    self.emit(
                        "Browser cleanup reported a warning; diagnostics suppressed.",
                        "warning",
                    )
            for logger in scopes:
                logger.removeHandler(handler)
            if accounting is not None:
                accounting.close()
            try:
                self._finish(run_id, success, category, interrupted=interrupted)
            except Exception:
                with self.lock:
                    self.state.status = "failed"
                    self.state.stage = "recovery_required"
                self.emit(
                    "Run finalization failed; manual recovery is required.",
                    "error",
                )
            finally:
                if self.provider_lease is not None:
                    self.provider_lease.close()
                    self.provider_lease = None


def _rest_message(seconds: float) -> str:
    return (
        f"Resting {seconds:.0f}s to stay polite to WaterlooWorks; "
        "this pause is scheduled, not an error."
    )


def _stored_listings(collection: ListingCollection) -> list[StoredListing]:
    listings: list[StoredListing] = []
    for posting_id, row in collection.postings.items():
        payload: dict[str, object] = {"id": posting_id}
        for cell in row.data:
            if cell.key not in EXPORT_FIELDS or cell.key == "Id":
                continue
            value = cell.value
            if cell.key == "JobTitle":
                if not isinstance(value, dict):
                    raise RuntimeError("Invalid title.")
                value = value.get("postingTitle")
            payload[EXPORT_FIELDS[cell.key]] = value
        listings.append(StoredListing.model_validate(payload))
    return listings


class SafeRefreshHandler(logging.Handler):
    def __init__(self, service: RefreshService) -> None:
        super().__init__()
        self.service = service

    def emit(self, record: logging.LogRecord) -> None:
        # Validate the exact counter format independently of the display text.
        if (
            record.name == "listings"
            and record.msg == PAGE_FORMAT
            and isinstance(record.args, tuple)
        ):
            numbers = [
                value
                for value in record.args[:6]
                if type(value) is int and 0 <= value <= 10**12
            ]
            if len(record.args) == 8 and len(numbers) == 6:
                with self.service.lock:
                    self.service.state.pages_completed = numbers[0]
                    self.service.state.total_pages = numbers[1]
                    self.service.state.records_received = numbers[3]
                    self.service.state.unique_postings = numbers[4]
                    self.service.state.duplicates = numbers[5]
                    self.service.state.eta_seconds = safe_eta_seconds(record.args[6])
        elif record.name == "listings" and record.msg in {
            "Page %d failed | category=%s | HTTP=%s",
        }:
            with self.service.lock:
                self.service.state.eta_seconds = None
        level = (
            "critical"
            if record.levelno >= logging.CRITICAL
            else "error"
            if record.levelno >= logging.ERROR
            else "warning"
            if record.levelno >= logging.WARNING
            else "info"
            if record.levelno >= logging.INFO
            else "debug"
        )
        self.service.emit(safe_message(record), level)


if __name__ == "__main__":
    raise SystemExit(main())
