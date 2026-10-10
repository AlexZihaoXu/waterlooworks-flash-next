"""Admin-only no-store usage metadata. GET never dispatches provider work."""

from pathlib import Path

from fastapi import Depends, FastAPI, Response
from pydantic import BaseModel, Field

from waterlooworks_flash_next.app_auth import require_admin
from waterlooworks_flash_next.openai_ledger import (
    AttemptLedger,
    BudgetLimits,
    BudgetStopped,
    daily_cost_stop_from_environment,
)
from waterlooworks_flash_next.openai_usage import PRICING_DATE, UsageCounters


class OpenAIRunUsage(BaseModel):
    run_id: str
    request_limit: int
    known_cost_stop_usd: str
    attempts: int
    unpriced_attempts: int
    known_usd: str
    total_usd: str | None
    input_tokens: int | None
    cached_tokens: int | None
    cache_write_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None


class OpenAIUsageStatus(BaseModel):
    pricing_version: str = PRICING_DATE
    default_request_limit: int
    default_known_cost_stop_usd: str
    runs: list[OpenAIRunUsage]
    daily_cost_stop_usd: str | None = Field(
        description="Optional rolling 24-hour known-cost stop across runs; null "
        "when WW_OPENAI_DAILY_COST_STOP_USD is unset or invalid."
    )
    daily_cost_stop_invalid: bool = Field(
        description="WW_OPENAI_DAILY_COST_STOP_USD is set but malformed or out of "
        "range; every new OpenAI reservation is refused until it is corrected."
    )
    rolling_24h_attempts: int
    rolling_24h_unpriced_attempts: int
    rolling_24h_known_usd: str
    rolling_24h_total_usd: str | None
    warning: str = (
        "Known-cost stop threshold is not a hard monetary cap. In-flight requests "
        "can add charges. Unknown usage requires operator review before a new run. "
        "Only the 20 most recent runs are listed; older ledger records are retained."
    )


def read_usage(path: Path) -> OpenAIUsageStatus:
    ledger = AttemptLedger(path)
    limits = BudgetLimits.from_environment()
    # A mistyped ceiling must stay visible here instead of hiding usage behind
    # a 500; reservations keep failing closed on it.
    try:
        daily_stop = daily_cost_stop_from_environment()
        daily_stop_invalid = False
    except BudgetStopped:
        daily_stop, daily_stop_invalid = None, True
    connection = ledger._connect()
    try:
        # One WAL read snapshot for run selection, prices and all token counters.
        # Closing below also ends the read transaction on errors.
        connection.execute("BEGIN")
        runs = []
        for row in connection.execute(
            "SELECT run_id,request_limit,known_cost_stop_usd FROM openai_runs "
            "ORDER BY created_at DESC LIMIT 20"
        ):
            summary = ledger._summary(connection, row[0])
            counters = [
                UsageCounters.model_validate_json(value[0])
                if value[0]
                else UsageCounters()
                for value in connection.execute(
                    "SELECT usage_json FROM openai_attempts WHERE run_id=?", (row[0],)
                )
            ]
            totals: dict[str, int | None] = {}
            for name in UsageCounters.model_fields:
                values = [getattr(usage, name) for usage in counters]
                totals[name] = (
                    None if any(value is None for value in values) else sum(values)
                )
            runs.append(
                OpenAIRunUsage(
                    run_id=row[0],
                    request_limit=row[1],
                    known_cost_stop_usd=row[2],
                    attempts=summary.attempts,
                    unpriced_attempts=summary.unpriced_attempts,
                    known_usd=str(summary.known_usd),
                    total_usd=None
                    if summary.total_usd is None
                    else str(summary.total_usd),
                    **totals,
                )
            )
        rolling = ledger.rolling_summary(connection)
        return OpenAIUsageStatus(
            default_request_limit=limits.request_limit,
            default_known_cost_stop_usd=str(limits.known_cost_stop_usd),
            runs=runs,
            daily_cost_stop_usd=None if daily_stop is None else str(daily_stop),
            daily_cost_stop_invalid=daily_stop_invalid,
            rolling_24h_attempts=rolling.attempts,
            rolling_24h_unpriced_attempts=rolling.unpriced_attempts,
            rolling_24h_known_usd=str(rolling.known_usd),
            rolling_24h_total_usd=None
            if rolling.total_usd is None
            else str(rolling.total_usd),
        )
    finally:
        connection.close()


def install_openai_usage(app: FastAPI, path: Path) -> None:
    @app.get(
        "/api/admin/openai-usage",
        dependencies=[Depends(require_admin)],
        response_model=OpenAIUsageStatus,
        operation_id="getOpenAIUsage",
    )
    def usage(response: Response) -> OpenAIUsageStatus:
        response.headers["Cache-Control"] = "no-store"
        return read_usage(path)
