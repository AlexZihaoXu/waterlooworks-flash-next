"""Dry-run extraction quality evaluation through the real Pi sidecar path.

    python -m waterlooworks_flash_next.pi_eval <database> --limit 20 \\
        [--reasoning low] [--max-turns 4] [--max-tool-calls 8] [--offset 0]

Uses the ACTIVE AI configuration (optionally overridden) on already-saved
postings that deterministic parsing left for analysis. The database is opened
read-only; nothing is written to inference tables, projections or the usage
ledger (an in-memory ledger stands in). The only possible write is persisting
a rotated OAuth refresh token, which must never be lost.

Output is JSON lines of fixed categories only: a posting index (never its ID
or text), outcome, failure category, fixed reason code, turns, tool calls,
token counts and seconds, then one aggregate summary line. Posting text,
evidence, model output and secrets are never printed.
"""

import argparse
import concurrent.futures
import json
import sqlite3
import statistics
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from waterlooworks_flash_next.ai_custom import validate_base_url
from waterlooworks_flash_next.ai_settings import AIConfig, AISettingsStore
from waterlooworks_flash_next.compact_history import revision_snapshot
from waterlooworks_flash_next.content import StoredPostingContent
from waterlooworks_flash_next.inference import _Item, submission_context
from waterlooworks_flash_next.inference_prompt import build_pi_prompt
from waterlooworks_flash_next.pi_bridge import PiItem, PiSession
from waterlooworks_flash_next.pi_submission import (
    SubmissionRejected,
    reason_code,
    validate_submission,
)

MAX_LIMIT = 500
Outcome = Literal["accepted", "rejected", "failed"]


class MemoryLedger:
    """Ledger stand-in for dry runs: reservations are unique per run, nothing
    is persisted, and every attempt is treated as already settled."""

    def __init__(self) -> None:
        self.reserved: set[str] = set()

    def reserve(
        self,
        run_id: str,
        operation_hash: str,
        *,
        active_attempts: frozenset[str] = frozenset(),
    ) -> str:
        key = f"{run_id}|{operation_hash}"
        if key in self.reserved:
            from waterlooworks_flash_next.openai_ledger import BudgetStopped

            raise BudgetStopped("Operation was already dispatched.")
        self.reserved.add(key)
        return str(uuid4())

    def finalize(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def finalize_priced(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def finalize_error_envelope(self, *_args: Any, **_kwargs: Any) -> None:
        return None


@dataclass(frozen=True)
class EvalRow:
    index: int
    outcome: Outcome
    category: str
    reason: str
    turns: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    seconds: float


_SELECT = """SELECT pointer.posting_id, results.compensation_state,
       results.skills_state, pointer.deterministic_projection_id,
       contents.content_hash,
       (SELECT max(revision_id) FROM posting_content_revisions r
          WHERE r.posting_id=pointer.posting_id) AS content_revision_id
  FROM posting_projection_current pointer
  JOIN analysis_projections deterministic
    ON deterministic.projection_id=pointer.deterministic_projection_id
  JOIN normalized_projection_results results
    ON results.projection_id=pointer.deterministic_projection_id
  JOIN posting_contents contents ON contents.posting_id=pointer.posting_id
 WHERE deterministic.content_hash=contents.content_hash
   AND (results.compensation_state='needs_analysis'
        OR results.skills_state='unknown')
 ORDER BY CAST(pointer.posting_id AS INTEGER), pointer.posting_id
 LIMIT ? OFFSET ?"""


def select_items(
    connection: sqlite3.Connection, config: AIConfig, limit: int, offset: int
) -> list[_Item]:
    """Postings deterministic parsing left for analysis, ignoring prior model
    results, so repeated evaluations compare the same work."""
    items = []
    for row in connection.execute(_SELECT, (limit, offset)).fetchall():
        content = StoredPostingContent.model_validate_json(
            revision_snapshot(connection, "content", row[5])
        )
        skills = tuple(
            skill[0]
            for skill in connection.execute(
                """SELECT catalog.canonical_name FROM normalized_skill_mentions m
                   JOIN normalized_skills catalog USING(skill_id)
                   WHERE m.projection_id=? AND m.excluded_reason IS NULL
                   ORDER BY m.ordinal""",
                (row[3],),
            )
        )
        compensation, skills_needed = row[1] == "needs_analysis", row[2] == "unknown"
        prompt, input_hash = build_pi_prompt(
            content.fields,
            request_compensation=compensation,
            request_skills=skills_needed,
            deterministic_skills=skills,
            config=config,
        )
        items.append(
            _Item(
                "eval",
                str(row[0]),
                row[4],
                input_hash,
                content.fields,
                prompt,
                compensation,
                skills_needed,
                skills,
            )
        )
    return items


def evaluate_item(session: PiSession, index: int, item: _Item) -> EvalRow:
    """One posting through the real sidecar; returns fixed categories only."""
    assert item.prompt is not None
    context = submission_context(item)
    last_reason = [""]

    def check(payload: dict[str, Any]) -> tuple[bool, str | None]:
        try:
            validate_submission(dict(payload), context)
            return True, None
        except SubmissionRejected as rejected:
            last_reason[0] = reason_code(rejected)
            return False, rejected.hint

    item_id = f"e-{index}"
    started = time.monotonic()
    try:
        result = session.run_item(
            PiItem(
                item_id=item_id,
                kind="extraction",
                system=item.prompt.system,
                user=item.prompt.user,
                sections=item.prompt.sections,
                submit_schema=item.prompt.submit_schema,
                operation_key=f"eval|{index}|{item.input_hash}",
                check=check,
            )
        )
    except Exception:
        result = None
    seconds = round(time.monotonic() - started, 2)
    metrics = session.item_metrics(item_id)
    outcome: Outcome
    if result is None:
        outcome, category, reason, turns, tools = "failed", "internal", "", 0, 0
    elif result.status == "submitted" and result.payload is not None:
        turns, tools = result.turns, result.tool_calls
        try:
            validate_submission(dict(result.payload), context)
            outcome, category, reason = "accepted", "", ""
        except SubmissionRejected as rejected:
            outcome, category = "rejected", "evidence_rejected"
            reason = reason_code(rejected)
    else:
        turns, tools = result.turns, result.tool_calls
        category = result.category or "internal"
        outcome = "rejected" if category in _MODEL_FAILURES else "failed"
        reason = last_reason[0]
    return EvalRow(
        index,
        outcome,
        category,
        reason,
        turns,
        tools,
        metrics.input_tokens,
        metrics.output_tokens,
        metrics.reasoning_tokens,
        seconds,
    )


_MODEL_FAILURES = frozenset(
    {"evidence_rejected", "max_turns", "max_tool_calls", "no_submission"}
)


def _median(values: list[float]) -> float:
    return round(statistics.median(values), 2) if values else 0.0


def summarize(
    rows: list[EvalRow], config: AIConfig, wall_seconds: float
) -> dict[str, Any]:
    attempted = len(rows)
    turns = [row.turns for row in rows]
    return {
        "summary": {
            "mode": config.access_mode,
            "model": config.model_id,
            "reasoning": config.reasoning_effort,
            "max_turns": config.max_turns,
            "max_tool_calls": config.max_tool_calls,
            "parallel": config.parallel_streams,
            "attempted": attempted,
            "accepted": sum(row.outcome == "accepted" for row in rows),
            "acceptance_rate": round(
                sum(row.outcome == "accepted" for row in rows) / attempted, 3
            )
            if attempted
            else 0.0,
            "outcomes": dict(Counter(row.outcome for row in rows)),
            "categories": dict(Counter(row.category for row in rows if row.category)),
            "reasons": dict(Counter(row.reason for row in rows if row.reason)),
            "turns_total": sum(turns),
            "turns_mean": round(sum(turns) / attempted, 2) if attempted else 0.0,
            "first_turn_submissions": sum(
                row.turns == 1 and row.outcome != "failed" for row in rows
            ),
            "tool_calls_total": sum(row.tool_calls for row in rows),
            "input_tokens_total": sum(row.input_tokens for row in rows),
            "output_tokens_total": sum(row.output_tokens for row in rows),
            "reasoning_tokens_total": sum(row.reasoning_tokens for row in rows),
            "input_tokens_per_turn_median": _median(
                [row.input_tokens / row.turns for row in rows if row.turns]
            ),
            "output_tokens_per_turn_median": _median(
                [row.output_tokens / row.turns for row in rows if row.turns]
            ),
            "reasoning_tokens_per_turn_median": _median(
                [row.reasoning_tokens / row.turns for row in rows if row.turns]
            ),
            "seconds_per_posting_median": _median([row.seconds for row in rows]),
            "wall_seconds": round(wall_seconds, 1),
        }
    }


def run_eval(
    items: list[_Item],
    session: PiSession,
    parallel: int,
    emit: Callable[[str], None] = print,
) -> list[EvalRow]:
    rows: list[EvalRow] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = [
            pool.submit(evaluate_item, session, index, item)
            for index, item in enumerate(items)
        ]
        for future in futures:
            row = future.result()
            rows.append(row)
            emit(json.dumps(asdict(row), separators=(",", ":")))
    return rows


def _overrides(args: argparse.Namespace, active: AIConfig) -> AIConfig:
    update: dict[str, Any] = {}
    for name, key in (
        ("model", "model_id"),
        ("reasoning", "reasoning_effort"),
        ("max_turns", "max_turns"),
        ("max_tool_calls", "max_tool_calls"),
        ("max_output_tokens", "max_output_tokens"),
        ("parallel", "parallel_streams"),
    ):
        value = getattr(args, name)
        if value is not None:
            update[key] = None if value == "default" else value
    return AIConfig.model_validate({**active.model_dump(), **update})


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dry-run Pi extraction evaluation (aggregate metrics only)."
    )
    parser.add_argument("database", type=Path)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument(
        "--access-mode",
        choices=("current",),
        default="current",
        help="Only the active access mode is supported.",
    )
    parser.add_argument("--model")
    parser.add_argument("--reasoning", help='Effort, or "default" for none.')
    parser.add_argument("--max-turns", type=int)
    parser.add_argument("--max-tool-calls", type=int)
    parser.add_argument("--max-output-tokens", type=int)
    parser.add_argument("--parallel", type=int)
    args = parser.parse_args()
    if not 1 <= args.limit <= MAX_LIMIT or args.offset < 0:
        parser.error(f"--limit must be 1..{MAX_LIMIT} and --offset >= 0")
    from waterlooworks_flash_next.runtime_secrets import (
        load_local_environment,
        runtime_value,
    )

    # Explicit operator command: use the local .env only when the key is not
    # already provided (containers pass WW_CREDENTIALS_KEY_FILE).
    if runtime_value("WW_CREDENTIALS_KEY") is None and (
        runtime_value("WW_CREDENTIALS_KEY_FILE") is None
    ):
        load_local_environment(Path(__file__).resolve().parents[3] / ".env")
    path = args.database.resolve(strict=True)
    try:
        active = AISettingsStore(path).active()
        if active is None or active.model_id is None:
            raise SystemExit("AI extraction is not activated.")
        config = _overrides(args, active)
    except SystemExit:
        raise
    except Exception:
        raise SystemExit(
            "AI settings are unavailable or an override is invalid."
        ) from None
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    connection.execute("PRAGMA query_only=ON")
    try:
        items = select_items(connection, config, args.limit, args.offset)
    finally:
        connection.close()
    endpoint = (
        validate_base_url(config.custom_base_url or "")
        if config.access_mode == "custom"
        else None
    )
    started = time.monotonic()
    with PiSession(
        path, config, str(uuid4()), endpoint=endpoint, ledger=MemoryLedger()
    ) as session:
        rows = run_eval(items, session, config.parallel_streams)
    print(
        json.dumps(
            summarize(rows, config, time.monotonic() - started), separators=(",", ":")
        )
    )


if __name__ == "__main__":
    main()
