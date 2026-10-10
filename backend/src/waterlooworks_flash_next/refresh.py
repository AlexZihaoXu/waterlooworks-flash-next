"""One explicit listings refresh at a time, with durable cooldown and safe events."""

import logging
import math
import sqlite3
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from waterlooworks_flash_next.api_models import RefreshLog, RefreshSnapshot
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
    DetailCollection,
    DetailFetchError,
    DetailProgress,
    DetailSource,
    record_blocked_until,
)
from waterlooworks_flash_next.duo import at_waterlooworks, submit_and_observe_duo
from waterlooworks_flash_next.export import EXPORT_FIELDS
from waterlooworks_flash_next.jobs import open_all_jobs
from waterlooworks_flash_next.listings import (
    FailureCategory,
    ListingCollection,
    ListingFetchError,
)
from waterlooworks_flash_next.logging import stage_logger
from waterlooworks_flash_next.login import AuthDeadline, LoginSession, start_login
from waterlooworks_flash_next.provider_lease import ProviderBusy, ProviderLease
from waterlooworks_flash_next.refresh_logging import (
    PAGE_FORMAT,
    safe_eta_seconds,
    safe_message,
)

COOLDOWN_SECONDS = 300


class RefreshAnalysisError(Exception):
    """Saved collection survives an unsuccessful OpenAI analysis phase."""


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


class RefreshConflict(Exception):
    def __init__(self, status_code: int, retry_after: int = 0) -> None:
        self.status_code = status_code
        self.retry_after = retry_after
        super().__init__(
            "Refresh already running."
            if status_code == 409
            else "Refresh cooldown is active."
        )


def claim_refresh(path: Path) -> str:
    """SQLite transaction prevents concurrent claims, including across API processes.

    Stale running rows are NOT automatically cleared after a crash.
    """
    connection = open_database(path)
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM collection_runs WHERE status='running' LIMIT 1"
            ).fetchone():
                raise RefreshConflict(409)
            blocked = connection.execute(
                "SELECT max(blocked_until) FROM collection_runs"
            ).fetchone()[0]
            if blocked:
                delay = math.ceil(
                    (
                        datetime.fromisoformat(blocked) - datetime.now(UTC)
                    ).total_seconds()
                )
                if delay > 0:
                    raise RefreshConflict(429, delay)
            latest = connection.execute(
                "SELECT finished_at FROM collection_runs WHERE finished_at IS NOT NULL "
                "ORDER BY finished_at DESC LIMIT 1"
            ).fetchone()
            now = datetime.now(UTC)
            if latest:
                remaining = (
                    COOLDOWN_SECONDS
                    - (now - datetime.fromisoformat(latest[0])).total_seconds()
                )
                if remaining > 0:
                    raise RefreshConflict(429, math.ceil(remaining))
            run_id = str(uuid4())
            connection.execute(
                "INSERT INTO collection_runs (id, started_at) VALUES (?, ?)",
                (run_id, now.isoformat(timespec="microseconds")),
            )
            return run_id
    finally:
        connection.close()


class RefreshService:
    def __init__(
        self,
        path: Path,
        project_root: Path | None,
        *,
        detail_limit: int | None = None,
    ) -> None:
        self.path = path
        self.project_root = project_root
        self.detail_limit = detail_limit
        self.lock = threading.RLock()
        self.state = RefreshSnapshot()
        self.worker: threading.Thread | None = None
        self.cooldown_until: datetime | None = None
        self.provider_lease: ProviderLease | None = None
        self.auth_challenge = AuthChallenge()

    def restore(self) -> None:
        """Restore cooldown/unfinished state without automatically restarting."""
        from datetime import timedelta

        connection = open_database(self.path)
        try:
            row = connection.execute(
                "SELECT id, status, pages_completed, records_received, "
                "unique_postings, duplicates, finished_at,details_completed,"
                "details_total,detail_requests,detail_retries,detail_limited "
                "FROM collection_runs "
                "ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if row:
                self.state = RefreshSnapshot.model_validate(
                    {
                        "run_id": row[0],
                        "status": row[1],
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
                        "detail_bursts": row[9] // 10,
                        "detail_limited": bool(row[11]),
                    }
                )
                if row[6]:
                    self.cooldown_until = datetime.fromisoformat(row[6]) + timedelta(
                        seconds=COOLDOWN_SECONDS
                    )
            blocked = connection.execute(
                "SELECT max(blocked_until) FROM collection_runs"
            ).fetchone()[0]
            if blocked:
                until = datetime.fromisoformat(blocked)
                self.cooldown_until = max(self.cooldown_until or until, until)
            if (
                row
                and connection.execute(
                    "SELECT 1 FROM inference_jobs WHERE job_id=?", (row[0],)
                ).fetchone()
            ):
                self._analysis_progress(connection, row[0], log=False)
        finally:
            connection.close()

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
            from waterlooworks_flash_next.openai_ledger import BudgetLimits
            from waterlooworks_flash_next.openai_provider import ResponsesProvider

            ResponsesProvider(self.path).require_ready()
            BudgetLimits.from_environment()
            lease = ProviderLease(self.path)
            try:
                lease.acquire()
            except ProviderBusy:
                raise RefreshConflict(409) from None
            try:
                run_id = claim_refresh(self.path)
            except Exception:
                lease.close()
                raise
            self.provider_lease = lease
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
        """Persist a listing-phase 429 Retry-After exactly like the detail phase."""
        if error.category != FailureCategory.RATE_LIMIT or error.retry_after is None:
            return
        try:
            connection = open_database(self.path)
            try:
                record_blocked_until(connection, run_id, error.retry_after)
            finally:
                connection.close()
        except Exception:
            self.emit(
                "Could not persist the server Retry-After; diagnostics suppressed.",
                "warning",
            )

    def _finish(self, run_id: str, success: bool, category: str | None) -> None:
        from datetime import timedelta

        now = datetime.now(UTC)
        connection = open_database(self.path)
        try:
            with connection:
                connection.execute(
                    "UPDATE collection_runs SET status=?, finished_at=?, "
                    "pages_completed=?, records_received=?, unique_postings=?, "
                    "duplicates=?, error_category=? WHERE id=?",
                    (
                        "completed" if success else "failed",
                        now.isoformat(timespec="microseconds"),
                        self.state.pages_completed,
                        self.state.records_received,
                        self.state.unique_postings,
                        self.state.duplicates,
                        category,
                        run_id,
                    ),
                )
            blocked = connection.execute(
                "SELECT blocked_until FROM collection_runs WHERE id=?", (run_id,)
            ).fetchone()[0]
        finally:
            connection.close()
        with self.lock:
            self.state.status = "completed" if success else "failed"
            self.state.stage = "complete" if success else "stopped"
            self.state.eta_seconds = None
            self.state.detail_wait_seconds = 0
            self.cooldown_until = now + timedelta(seconds=COOLDOWN_SECONDS)
            if blocked:
                self.cooldown_until = max(
                    self.cooldown_until, datetime.fromisoformat(blocked)
                )
        self.emit(
            (
                "Refresh completed with the server detail cap; "
                "remaining details were not requested."
                if self.state.detail_limited
                else "Collection and OpenAI extraction completed."
                if self.state.analysis_job_id is not None
                else "Listing and detail-content refresh completed."
            )
            if success
            else "Refresh stopped; completed checkpoints are preserved. "
            "No automatic restart.",
            "info" if success else "error",
        )

    def _analysis_progress(
        self, connection: sqlite3.Connection, job_id: str, *, log: bool = True
    ) -> None:
        from waterlooworks_flash_next.inference import inference_job_status

        status = inference_job_status(connection, job_id)
        counts = status.counts
        total = connection.execute("SELECT count(*) FROM postings").fetchone()[0]
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
                f"OpenAI extraction: {completed}/{total} processed; "
                f"{accepted} accepted, {rejected} rejected, {failed} failed, "
                f"{skipped} skipped; {cache_hits} cache hits."
            )

    def _analyze_saved_data(self, run_id: str) -> None:
        from waterlooworks_flash_next.inference import (
            InferenceConfig,
            create_inference_job,
            inference_job_status,
            run_inference_job,
        )
        from waterlooworks_flash_next.inference_schema import _ERROR_CATEGORIES
        from waterlooworks_flash_next.normalized_storage import normalize_saved_postings
        from waterlooworks_flash_next.openai_usage import MODEL as MODEL_ID

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
            ids = [
                str(row[0])
                for row in connection.execute(
                    "SELECT posting_id FROM posting_contents ORDER BY posting_id"
                )
            ]
            total = connection.execute("SELECT count(*) FROM postings").fetchone()[0]
            if not ids:
                with self.lock:
                    self.state.analysis_total = total
                    self.state.analysis_completed = total
                    self.state.analysis_skipped = total
                self.emit("No saved detail content to analyze; no model request made.")
                return
            job_id = create_inference_job(
                connection, ids, InferenceConfig(concurrency=4), job_id=run_id
            )
            with self.lock:
                self.state.stage = "analysis"
                self.state.analysis_total = total
                self.state.analysis_job_id = job_id
            self.emit(
                f"OpenAI extraction job {job_id}: {len(ids)} saved postings queued; "
                f"{total - len(ids)} without saved details skipped. Model {MODEL_ID}; "
                "at most 4 simultaneous requests; validated cache reused."
            )

            def progress() -> None:
                self._analysis_progress(connection, job_id)

            progress()
            if inference_job_status(connection, job_id).persisted_status == "pending":
                run_inference_job(
                    connection,
                    job_id,
                    on_progress=progress,
                    provider_lease=self.provider_lease,
                )
            progress()
            status = inference_job_status(connection, job_id)
            for reason, count in connection.execute(
                "SELECT error_category,count(*) FROM inference_job_items "
                "WHERE job_id=? AND error_category IS NOT NULL GROUP BY error_category",
                (job_id,),
            ):
                if reason in _ERROR_CATEGORIES:
                    self.emit(
                        f"OpenAI extraction outcome: {reason}; {count} postings.",
                        "warning",
                    )
            if status.error_category in _ERROR_CATEGORIES:
                self.emit(
                    f"OpenAI extraction failure category: {status.error_category}.",
                    "error",
                )
            if status.persisted_status != "completed":
                self.emit(
                    "OpenAI extraction did not complete. Saved collection and accepted "
                    "checkpoints are preserved. Check OpenAI usage, budget "
                    "and the saved job "
                    "status; no automatic resume.",
                    "error",
                )
                raise RefreshAnalysisError("OpenAI analysis did not complete.")
            self.emit(
                "OpenAI extraction completed. Rejected or unavailable facts remain "
                "unknown; no model reasoning or posting content was logged.",
                "warning" if status.counts.get("rejected") else "info",
            )
        except RefreshAnalysisError:
            raise
        except Exception:
            self.emit(
                "OpenAI analysis failed; saved collection is preserved. "
                "Diagnostics suppressed.",
                "error",
            )
            raise RefreshAnalysisError("OpenAI analysis failed.") from None
        finally:
            connection.close()

    def _detail_progress(self, progress: DetailProgress) -> None:
        with self.lock:
            self.state.stage = progress.stage
            self.state.details_completed = progress.completed
            self.state.details_total = progress.total
            self.state.detail_requests = progress.requests
            self.state.detail_retries = progress.retries
            self.state.detail_bursts = progress.bursts
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
            self.emit(f"Detail burst rest; waiting {progress.wait_seconds:.1f}s.")
        else:
            self.emit(
                f"Detail progress: {progress.completed}/{progress.total} saved; "
                f"{progress.requests} requests; {progress.retries} retries; "
                f"{progress.bursts} bursts."
            )

    def _run(self, run_id: str) -> None:
        session: LoginSession | None = None
        success = False
        category: str | None = "internal"
        handler = SafeRefreshHandler(self)
        scopes = [stage_logger(scope) for scope in ("login", "duo", "jobs", "listings")]
        for logger in scopes:
            logger.addHandler(handler)
        try:
            assert self.project_root is not None
            deadline = AuthDeadline()
            self.auth_challenge.begin(run_id, deadline)
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
            source = open_all_jobs(session.page)
            if source is None:
                raise RuntimeError("No fresh listing source.")
            # Suppress browser background egress while direct listings run.
            install_capture_guard(session.page, "")
            with self.lock:
                self.state.stage = "listings"
                self.state.total_pages = max(
                    1, math.ceil(source.first_page.totalResults / source.page_size)
                )
            collection = ListingCollection(source)
            collection.run()  # Captured page 1 reused; existing 2/4/8-second policy.
            with self.lock:
                self.state.stage = "saving"
                self.state.eta_seconds = None
            self.emit("Saving the completed listing pass and its revision history.")
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
            connection = open_database(self.path)
            try:
                save_listings(
                    connection, run_id, listings, received_at=collection.received_at
                )
                queue = (
                    listings
                    if self.detail_limit is None
                    else listings[: self.detail_limit]
                )
                limited = len(queue) < len(listings)
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
                )
                details.run()
            finally:
                connection.close()
            if session is not None:
                session.close()
                session = None
            self._analyze_saved_data(run_id)
            success, category = True, None
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
            try:
                self._finish(run_id, success, category)
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
            "Retrying the same page after %.0fs from failure completion.",
            "Page %d attempt %d/%d failed | category=%s | HTTP=%s | retry=%s",
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
