"""Durable per-dispatch metadata and conservative known-cost stop thresholds."""

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from waterlooworks_flash_next.openai_usage import (
    PRICING_DATE,
    CostSummary,
    UsageCounters,
    price_usage,
    summarize_costs,
)
from waterlooworks_flash_next.runtime_secrets import runtime_value

ERROR_ENVELOPE_PRICING = "error-envelope-http"
# Upper bound on simultaneously reserved (unfinalized) attempts per run; equals
# the largest configurable number of parallel Pi streams.
MAX_IN_FLIGHT = 8


class BudgetStopped(Exception):
    """No new dispatch; prior usage may be unknown, never assumed free."""


def _setting(name: str, default: str) -> str:
    value = runtime_value(name)
    return default if value is None else value


class BudgetLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    request_limit: int = Field(default=20, ge=1, le=10000, strict=True)
    known_cost_stop_usd: Decimal = Field(
        default=Decimal("1"), ge=Decimal("0.01"), le=100
    )

    @classmethod
    def from_environment(cls) -> "BudgetLimits":
        try:
            return cls(
                request_limit=int(_setting("WW_OPENAI_REQUEST_LIMIT", "20")),
                known_cost_stop_usd=Decimal(
                    _setting("WW_OPENAI_KNOWN_COST_STOP_USD", "1")
                ),
            )
        except Exception:
            raise BudgetStopped("OpenAI budget configuration is invalid.") from None


DAILY_COST_STOP_MAX_USD = Decimal(1000)


def daily_cost_stop_from_environment() -> Decimal | None:
    """Optional rolling 24-hour known-cost stop across all runs; unset disables."""
    raw = runtime_value("WW_OPENAI_DAILY_COST_STOP_USD")
    if raw is None or raw == "":
        return None  # Empty supports Compose pass-through of an unset value.
    try:
        value = Decimal(raw)
        if not value.is_finite() or not Decimal("0.01") <= value <= (
            DAILY_COST_STOP_MAX_USD
        ):
            raise ValueError
    except Exception:
        raise BudgetStopped("OpenAI budget configuration is invalid.") from None
    return value


def migrate_openai_ledger(connection: sqlite3.Connection) -> None:
    connection.execute("""
        CREATE TABLE openai_runs (
            run_id TEXT PRIMARY KEY NOT NULL,
            created_at TEXT NOT NULL,
            request_limit INTEGER NOT NULL CHECK(request_limit BETWEEN 1 AND 10000),
            known_cost_stop_usd TEXT NOT NULL
        ) STRICT
    """)
    connection.execute("""
        CREATE TABLE openai_attempts (
            attempt_id TEXT PRIMARY KEY NOT NULL,
            run_id TEXT NOT NULL REFERENCES openai_runs(run_id),
            operation_hash TEXT NOT NULL CHECK(length(operation_hash)=64
                AND operation_hash NOT GLOB '*[^0-9a-f]*'),
            reserved_at TEXT NOT NULL,
            finalized_at TEXT,
            model TEXT,
            service_tier TEXT,
            pricing_version TEXT NOT NULL,
            usage_json TEXT CHECK(usage_json IS NULL OR json_valid(usage_json)),
            known_usd TEXT,
            UNIQUE(run_id,operation_hash)
        ) STRICT
    """)
    connection.execute(
        "CREATE INDEX idx_openai_attempts_run ON openai_attempts(run_id)"
    )


class AttemptLedger:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path.resolve().as_uri() + "?mode=rw", uri=True, timeout=5
        )
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def create_run(self, run_id: str, limits: BudgetLimits) -> None:
        if str(UUID(run_id)) != run_id:
            raise BudgetStopped("OpenAI run identity is invalid.")
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    (
                        "INSERT INTO openai_runs VALUES(?,?,?,?) ON "
                        "CONFLICT(run_id) DO NOTHING"
                    ),
                    (
                        run_id,
                        datetime.now(UTC).isoformat(),
                        limits.request_limit,
                        str(limits.known_cost_stop_usd),
                    ),
                )
        finally:
            connection.close()

    @staticmethod
    def _summary(connection: sqlite3.Connection, run_id: str) -> CostSummary:
        return summarize_costs(
            {
                row[0]: None if row[1] is None else Decimal(row[1])
                for row in connection.execute(
                    "SELECT attempt_id,known_usd FROM openai_attempts WHERE run_id=?",
                    (run_id,),
                )
            }
        )

    @staticmethod
    def _rolling_rows(
        connection: sqlite3.Connection,
    ) -> list[tuple[str, str | None, str | None]]:
        return connection.execute(
            "SELECT attempt_id,known_usd,finalized_at FROM openai_attempts "
            "WHERE julianday(reserved_at) >= julianday('now','-1 day')"
        ).fetchall()

    @classmethod
    def rolling_summary(cls, connection: sqlite3.Connection) -> CostSummary:
        """Every attempt reserved in the last 24 hours, across all runs."""
        return summarize_costs(
            {
                row[0]: None if row[1] is None else Decimal(row[1])
                for row in cls._rolling_rows(connection)
            }
        )

    def summary(self, run_id: str) -> CostSummary:
        connection = self._connect()
        try:
            return self._summary(connection, run_id)
        finally:
            connection.close()

    def require_resumable(self, run_id: str) -> None:
        if self.summary(run_id).unpriced_attempts:
            raise BudgetStopped(
                "OpenAI usage is unknown; review before an explicit new run."
            )

    def reserve(
        self,
        run_id: str,
        operation_hash: str,
        *,
        active_attempts: frozenset[str] = frozenset(),
    ) -> str:
        daily_stop = daily_cost_stop_from_environment()
        connection = self._connect()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                limits = connection.execute(
                    (
                        "SELECT request_limit,known_cost_stop_usd FROM "
                        "openai_runs WHERE run_id=?"
                    ),
                    (run_id,),
                ).fetchone()
                if limits is None:
                    raise BudgetStopped("OpenAI run budget is missing.")
                summary = self._summary(connection, run_id)
                unknown = connection.execute(
                    (
                        "SELECT attempt_id,finalized_at FROM openai_attempts "
                        "WHERE run_id=? AND known_usd IS NULL"
                    ),
                    (run_id,),
                ).fetchall()
                if any(
                    row[1] is not None or row[0] not in active_attempts
                    for row in unknown
                ):
                    raise BudgetStopped(
                        "OpenAI usage is unknown; review before an explicit new run."
                    )
                if (
                    len(unknown) >= MAX_IN_FLIGHT
                    or summary.attempts >= limits[0]
                    or summary.known_usd >= Decimal(limits[1])
                ):
                    raise BudgetStopped(
                        "OpenAI request limit or known-cost stop threshold reached."
                    )
                if daily_stop is not None:
                    rolling = self._rolling_rows(connection)
                    if any(
                        known is None
                        and (finalized is not None or attempt not in active_attempts)
                        for attempt, known, finalized in rolling
                    ):
                        raise BudgetStopped(
                            "OpenAI usage in the last 24 hours is unknown; review "
                            "before dispatching under the daily stop threshold."
                        )
                    if self.rolling_summary(connection).known_usd >= daily_stop:
                        raise BudgetStopped(
                            "OpenAI rolling 24-hour known-cost stop threshold reached."
                        )
                if connection.execute(
                    "SELECT 1 FROM openai_attempts WHERE run_id=? AND operation_hash=?",
                    (run_id, operation_hash),
                ).fetchone():
                    raise BudgetStopped(
                        "OpenAI operation was already dispatched; it will not "
                        "be resent."
                    )
                attempt = str(uuid4())
                connection.execute(
                    (
                        "INSERT INTO "
                        "openai_attempts(attempt_id,run_id,operation_hash,reser"
                        "ved_at,pricing_version) VALUES(?,?,?,?,?)"
                    ),
                    (
                        attempt,
                        run_id,
                        operation_hash,
                        datetime.now(UTC).isoformat(),
                        PRICING_DATE,
                    ),
                )
                return attempt
        finally:
            connection.close()

    def finalize(
        self,
        attempt_id: str,
        usage: UsageCounters,
        *,
        model: str | None,
        service_tier: str | None,
    ) -> None:
        cost = price_usage(usage, model=model or "", service_tier=service_tier or "")
        connection = self._connect()
        try:
            with connection:
                # Final response is immutable and idempotent; late duplicates cannot
                # erase recorded usage. No streaming API is enabled by this client.
                connection.execute(
                    (
                        "UPDATE openai_attempts SET "
                        "finalized_at=?,model=?,service_tier=?,usage_json=?,kno"
                        "wn_usd=? WHERE attempt_id=? AND finalized_at IS NULL"
                    ),
                    (
                        datetime.now(UTC).isoformat(),
                        model,
                        service_tier,
                        usage.model_dump_json(),
                        None if cost is None else str(cost),
                        attempt_id,
                    ),
                )
        finally:
            connection.close()

    def finalize_priced(
        self,
        attempt_id: str,
        usage: UsageCounters,
        *,
        model: str | None,
        known_usd: Decimal | None,
        pricing_version: str,
    ) -> None:
        """Record usage with an explicitly determined cost (or unknown).

        Subscription and custom modes record a known $0 with their own pricing
        version; API-key models outside the local price table use pi-ai's
        catalog estimate. `None` keeps the attempt unknown, never assumed free.
        """
        if known_usd is not None and (not known_usd.is_finite() or known_usd < 0):
            raise ValueError("Known cost is invalid.")
        if not 1 <= len(pricing_version) <= 64:
            raise ValueError("Pricing version is invalid.")
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    (
                        "UPDATE openai_attempts SET "
                        "finalized_at=?,model=?,service_tier=NULL,usage_json=?,"
                        "known_usd=?,pricing_version=? "
                        "WHERE attempt_id=? AND finalized_at IS NULL"
                    ),
                    (
                        datetime.now(UTC).isoformat(),
                        model,
                        usage.model_dump_json(),
                        None if known_usd is None else str(known_usd),
                        pricing_version,
                        attempt_id,
                    ),
                )
        finally:
            connection.close()

    def finalize_error_envelope(self, attempt_id: str, status: int) -> None:
        """Record a usage-less non-2xx OpenAI error body as a known $0 attempt.

        Rejected requests are not billed and report zero tokens. The marker in
        `pricing_version` distinguishes these from priced completions. Timeouts and
        transport loss never use this path; they stay unknown.
        """
        if type(status) is not int or not 300 <= status <= 599:
            raise ValueError("Error envelope status is invalid.")
        zero = UsageCounters(
            input_tokens=0,
            cached_tokens=0,
            cache_write_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
        )
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    (
                        "UPDATE openai_attempts SET "
                        "finalized_at=?,model=NULL,service_tier=NULL,usage_json=?,"
                        "known_usd='0',pricing_version=? "
                        "WHERE attempt_id=? AND finalized_at IS NULL"
                    ),
                    (
                        datetime.now(UTC).isoformat(),
                        zero.model_dump_json(),
                        f"{ERROR_ENVELOPE_PRICING}-{status}",
                        attempt_id,
                    ),
                )
        finally:
            connection.close()
