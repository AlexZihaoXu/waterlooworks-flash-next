"""Admin-only no-store usage metadata. GET never dispatches provider work."""

from pathlib import Path

from fastapi import Depends, FastAPI, Response
from pydantic import BaseModel

from waterlooworks_flash_next.app_auth import require_admin
from waterlooworks_flash_next.openai_ledger import AttemptLedger, BudgetLimits
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
    warning: str = (
        "Known-cost stop threshold is not a hard monetary cap. In-flight requests "
        "can add charges. Unknown usage requires operator review before a new run. "
        "Only the 20 most recent runs are listed; older ledger records are retained."
    )


def read_usage(path: Path) -> OpenAIUsageStatus:
    ledger = AttemptLedger(path)
    limits = BudgetLimits.from_environment()
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
        return OpenAIUsageStatus(
            default_request_limit=limits.request_limit,
            default_known_cost_stop_usd=str(limits.known_cost_stop_usd),
            runs=runs,
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
