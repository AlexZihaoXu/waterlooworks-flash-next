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
                    len(unknown) >= 4
                    or summary.attempts >= limits[0]
                    or summary.known_usd >= Decimal(limits[1])
                ):
                    raise BudgetStopped(
                        "OpenAI request limit or known-cost stop threshold reached."
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
