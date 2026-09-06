"""One explicit listings refresh at a time, with durable cooldown and safe events."""

import logging
import math
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from waterlooworks_flash_next.api_models import RefreshLog, RefreshSnapshot
from waterlooworks_flash_next.database import (
    StoredListing,
    open_database,
    save_listings,
)
from waterlooworks_flash_next.duo import at_waterlooworks, submit_and_observe_duo
from waterlooworks_flash_next.export import EXPORT_FIELDS
from waterlooworks_flash_next.jobs import open_all_jobs
from waterlooworks_flash_next.listings import ListingCollection, ListingFetchError
from waterlooworks_flash_next.logging import stage_logger
from waterlooworks_flash_next.login import LoginSession, start_login
from waterlooworks_flash_next.refresh_logging import (
    PAGE_FORMAT,
    safe_eta_seconds,
    safe_message,
)

COOLDOWN_SECONDS = 300


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
                (run_id, now.isoformat()),
            )
            return run_id
    finally:
        connection.close()


class RefreshService:
    def __init__(self, path: Path, credentials_path: Path | None) -> None:
        self.path = path
        self.credentials_path = credentials_path
        self.lock = threading.RLock()
        self.state = RefreshSnapshot()
        self.worker: threading.Thread | None = None
        self.cooldown_until: datetime | None = None

    def restore(self) -> None:
        """Restore cooldown/unfinished state without automatically restarting."""
        from datetime import timedelta

        connection = open_database(self.path)
        try:
            row = connection.execute(
                "SELECT id, status, pages_completed, records_received, "
                "unique_postings, duplicates, finished_at FROM collection_runs "
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
                    }
                )
                if row[6]:
                    self.cooldown_until = datetime.fromisoformat(row[6]) + timedelta(
                        seconds=COOLDOWN_SECONDS
                    )
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
            if self.credentials_path is None:
                raise RuntimeError("Manual refresh is disabled.")
            run_id = claim_refresh(self.path)
            self.state = RefreshSnapshot(
                run_id=run_id, status="running", stage="authentication"
            )
            self.emit(
                "Fresh authentication starting. Approve Duo in the browser if needed."
            )
            self.worker = threading.Thread(
                target=self._run, args=(run_id,), name="listing-refresh", daemon=False
            )
            try:
                self.worker.start()
            except Exception:
                self._finish(run_id, False, "internal")
                raise RuntimeError("Could not start refresh worker.") from None
            return run_id

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
                        now.isoformat(),
                        self.state.pages_completed,
                        self.state.records_received,
                        self.state.unique_postings,
                        self.state.duplicates,
                        category,
                        run_id,
                    ),
                )
        finally:
            connection.close()
        with self.lock:
            self.state.status = "completed" if success else "failed"
            self.state.stage = "complete" if success else "stopped"
            self.state.eta_seconds = None
            self.cooldown_until = now + timedelta(seconds=COOLDOWN_SECONDS)
        self.emit(
            "Listing refresh completed. No detail requests were made."
            if success
            else "Refresh stopped; no automatic restart or partial listing save.",
            "info" if success else "error",
        )

    def _run(self, run_id: str) -> None:
        session: LoginSession | None = None
        success = False
        category: str | None = "internal"
        handler = SafeRefreshHandler(self)
        scopes = [stage_logger(scope) for scope in ("login", "duo", "jobs", "listings")]
        scrubber = DuoScrubber()
        scopes[1].addFilter(scrubber)
        for logger in scopes:
            logger.addHandler(handler)
        try:
            assert self.credentials_path is not None
            session = start_login(self.credentials_path)
            submit_and_observe_duo(session)
            if not at_waterlooworks(session.page):
                raise RuntimeError("Authentication not confirmed.")
            source = open_all_jobs(session.page)
            if source is None:
                raise RuntimeError("No fresh listing source.")
            # The source owns copied request fields/cookies and captured page 1;
            # direct HTTP collection must not keep the login browser alive.
            session.close()
            session = None
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
                save_listings(connection, run_id, listings)
            finally:
                connection.close()
            success, category = True, None
        except ListingFetchError as error:
            category = error.category.value
        except (Exception, SystemExit):
            # Includes the existing Duo timeout handler's SystemExit; no relogin.
            category = "internal"
        finally:
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
            scopes[1].removeFilter(scrubber)
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


class DuoScrubber(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if str(record.msg).startswith("Detected verification code:"):
            record.msg = "Duo approval required; use the code in the browser."
            record.args = ()
        return True


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
