"""Explicit bounded work-country predictions with the active Pi AI configuration.

Python selects postings, builds the location-only input, validates every
submission (exact quotes, ambiguity rules, confidence floor) and writes
checkpoints; the sidecar only runs the agent. Predictions stay separate from
verified source facts. Operator CLI only; never runs on import or reads.

    python -m waterlooworks_flash_next.country_runner <database> start --all
"""

import argparse
import concurrent.futures
import hashlib
import json
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from waterlooworks_flash_next.ai_custom import validate_base_url
from waterlooworks_flash_next.ai_settings import AIConfig
from waterlooworks_flash_next.country_inference import read_country_inferences
from waterlooworks_flash_next.country_schema import COUNTRIES, COUNTRY_VERSION
from waterlooworks_flash_next.database import open_database
from waterlooworks_flash_next.inference import active_config, require_ready, run_limits
from waterlooworks_flash_next.inference_prompt import (
    PI_TEMPLATE_VERSION,
    config_hash,
    config_identity,
)
from waterlooworks_flash_next.openai_ledger import AttemptLedger
from waterlooworks_flash_next.pi_bridge import PiItem, PiSession
from waterlooworks_flash_next.provider_lease import ProviderLease

PROMPT = Path(__file__).with_name("country-prompt.txt").read_text(encoding="utf-8")
COUNTRY_TOOL_INSTRUCTIONS = (
    "The supplied location fields are inline and also available through "
    "list_sections, read_section and find_span. Finish by calling submit_country "
    "exactly once; never answer in prose."
)
LOCATION_FIELDS = (
    "Job - City:",
    "Job - Province/State:",
    "Job - Country:",
    "Job - Address Line One:",
    "Job - Address Line Two:",
    "Employment Location Arrangement:",
    "Additional Employment Arrangement Location Information:",
    "Job Location (If Exact Address Unknown or Multiple Locations):",
)
REASONS = (
    "location_context",
    "ambiguous_location",
    "no_location_context",
    "multiple_countries",
    "low_confidence",
)
MAX_INPUT_BYTES = 24_000
LEASE_MS = 120_000
_GLOBAL = re.compile(
    r"^(?:remote[\s,:-]+)?(?:worldwide|anywhere(?: in the world)?|globally|"
    r"work from anywhere)[.!]?$"
)
_INADEQUATE = re.compile(
    r"headquarters|head office|citizenship|nationality|eligible to apply|salary|"
    r"compensation|\bHQ\b",
    re.I,
)


class CountryError(Exception):
    """Fixed diagnostics only."""


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def country_input(
    listing: dict[str, Any], content: dict[str, Any]
) -> tuple[dict[str, str], str | None]:
    fields: dict[str, str] = {}
    city = listing.get("city")
    if isinstance(city, str) and city.strip():
        fields["listing:city"] = city
    for name in (*LOCATION_FIELDS, "Job Summary:", "Job Responsibilities:"):
        value = content.get(name)
        if isinstance(value, str) and value.strip():
            fields[f"content:{name}"] = value
    encoded = json.dumps(fields, ensure_ascii=False, separators=(",", ":")).encode()
    # Whole relevant fields only: oversize is a terminal unknown, never truncated.
    if len(encoded) > MAX_INPUT_BYTES:
        return fields, "input_too_large"
    return fields, None if fields else "no_location_context"


def location_ambiguity(values: list[str]) -> bool:
    locations = {
        value.strip().lower()
        for value in values
        if isinstance(value, str)
        and value.strip()
        and value.strip().lower() not in ("remote", "hybrid", "in-person")
    }
    if any(_GLOBAL.match(value) for value in locations):
        return True
    return len(locations) == 1 and bool(
        re.match(r"^london[.!]?$", next(iter(locations)))
    )


def _exact(source: str, quote: str) -> tuple[int, int] | None:
    if not quote.strip() or len(quote) > 500:
        return None
    offset = source.find(quote)
    if offset < 0 or source.find(quote, offset + 1) != -1:
        return None
    return offset, offset + len(quote)


def country_schema(fields: dict[str, str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["country", "confidence", "reason", "evidence"],
        "properties": {
            "country": {"enum": [None, *COUNTRIES]},
            "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
            "reason": {"enum": list(REASONS)},
            "evidence": {
                "type": "array",
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["field", "quote"],
                    "properties": {
                        "field": {"type": "string", "enum": list(fields)},
                        "quote": {"type": "string", "minLength": 1, "maxLength": 500},
                    },
                },
            },
        },
    }


def validate_country(answer: object, fields: dict[str, str]) -> dict[str, Any]:
    """Port of the reviewed country validator; raises ValueError when invalid."""
    if (
        not isinstance(answer, dict)
        or set(answer) != {"country", "confidence", "reason", "evidence"}
        or answer["reason"] not in REASONS
        or not isinstance(answer["evidence"], list)
        or len(answer["evidence"]) > 3
    ):
        raise ValueError("invalid_output")
    unknown: dict[str, Any] = {
        "status": "unknown",
        "country": None,
        "confidence": None,
        "evidence": [],
    }
    if answer["country"] is None:
        if (
            answer["confidence"] is not None
            or answer["evidence"]
            or answer["reason"] == "location_context"
        ):
            raise ValueError("invalid_output")
        return unknown | {"reason": answer["reason"]}
    code, confidence = answer["country"], answer["confidence"]
    if (
        not isinstance(code, str)
        or code not in COUNTRIES
        or isinstance(confidence, bool)
        or not isinstance(confidence, int | float)
        or not 0 <= confidence <= 1
        or answer["reason"] != "location_context"
        or not answer["evidence"]
    ):
        raise ValueError("invalid_output")
    evidence = []
    for entry in answer["evidence"]:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"field", "quote"}
            or entry["field"] not in fields
            or not isinstance(entry["quote"], str)
        ):
            raise ValueError("invalid_evidence")
        span = _exact(fields[entry["field"]], entry["quote"])
        if span is None or _INADEQUATE.search(entry["quote"]):
            raise ValueError("invalid_evidence")
        evidence.append(
            {
                "field": entry["field"],
                "start": span[0],
                "end": span[1],
                "quote_hash": _hash(entry["quote"]),
            }
        )
    explicit = [
        value
        for key, value in fields.items()
        if (key == "listing:city" or key in {f"content:{f}" for f in LOCATION_FIELDS})
        and not re.match(r"^london[.!]?$", value.strip(), re.I)
    ]
    if location_ambiguity(explicit) or location_ambiguity(
        [entry["quote"] for entry in answer["evidence"]]
    ):
        return unknown | {"reason": "ambiguous_location"}
    if confidence < 0.85:
        return unknown | {"reason": "low_confidence"}
    return {
        "status": "accepted",
        "country": code,
        "confidence": float(confidence),
        "reason": "location_context",
        "evidence": evidence,
    }


def run_configuration(ai: AIConfig) -> tuple[str, str]:
    configuration = {
        "version": COUNTRY_VERSION,
        "provider": "pi",
        "providerVersion": PI_TEMPLATE_VERSION,
        "configuration_hash": config_hash(ai),
        "pi": config_identity(ai),
        "model": {"id": ai.model_id},
        "reasoning": ai.reasoning_effort,
        "prompt_hash": _hash(PROMPT),
        "tool_prompt_hash": _hash(COUNTRY_TOOL_INSTRUCTIONS),
        "countries_hash": _hash(
            json.dumps(COUNTRIES, ensure_ascii=False, separators=(",", ":"))
        ),
    }
    raw = json.dumps(configuration, separators=(",", ":"))
    return raw, _hash(raw)


def pi_configuration_valid(config: dict[str, Any]) -> bool:
    return bool(
        config.get("provider") == "pi"
        and config.get("version") == COUNTRY_VERSION
        and config.get("providerVersion") == PI_TEMPLATE_VERSION
        and config.get("prompt_hash") == _hash(PROMPT)
        and config.get("tool_prompt_hash") == _hash(COUNTRY_TOOL_INSTRUCTIONS)
        and config.get("countries_hash")
        == _hash(json.dumps(COUNTRIES, ensure_ascii=False, separators=(",", ":")))
        and isinstance(config.get("model", {}).get("id"), str)
    )


_SOURCE = """SELECT p.posting_id,p.listing_json,p.content_hash listing_hash,
 c.content_json,c.content_hash,
 (SELECT max(revision_id) FROM posting_revisions r WHERE r.posting_id=p.posting_id
    AND r.content_hash=p.content_hash) listing_revision_id,
 (SELECT max(revision_id) FROM posting_content_revisions r
    WHERE r.posting_id=p.posting_id AND r.content_hash=c.content_hash)
    content_revision_id
 FROM postings p LEFT JOIN posting_contents c USING(posting_id)"""


def _input_hash(row: sqlite3.Row) -> str:
    return _hash(
        json.dumps([row["listing_hash"], row["content_hash"]], separators=(",", ":"))
    )


def _fields(row: sqlite3.Row) -> tuple[dict[str, str], str | None]:
    listing = json.loads(row["listing_json"])
    content = json.loads(row["content_json"])["fields"] if row["content_json"] else {}
    return country_input(listing, content)


def begin_run(
    connection: sqlite3.Connection,
    ai: AIConfig,
    posting_ids: list[str] | None,
    limit: int,
) -> str:
    """Create a run over postings without a current valid prediction."""
    raw, digest = run_configuration(ai)
    current = read_country_inferences(connection)
    run_id = str(uuid4())
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        now_ms = int(time.time() * 1000)
        if connection.execute(
            "SELECT 1 FROM country_inference_runs WHERE status='running' "
            "AND lease_until_ms>=?",
            (now_ms,),
        ).fetchone():
            raise CountryError("Another country run is active.")
        connection.execute(
            "UPDATE country_inference_runs SET status='stopped',owner=NULL "
            "WHERE status='running' AND lease_until_ms<?",
            (now_ms,),
        )
        connection.execute(
            "INSERT INTO country_inference_runs VALUES(?,?,?,'running',?,?,0,?,?)",
            (run_id, digest, raw, None, now_ms + LEASE_MS, _now(), _now()),
        )
        selected = 0
        for row in connection.execute(
            _SOURCE + " ORDER BY CAST(p.posting_id AS INTEGER),p.posting_id"
        ).fetchall():
            if posting_ids is not None and row["posting_id"] not in posting_ids:
                continue
            existing = current.get(row["posting_id"])
            if (
                posting_ids is None
                and existing is not None
                and existing.state
                in (
                    "accepted",
                    "unknown",
                )
            ):
                continue
            if selected >= limit:
                break
            selected += 1
            fields, reason = _fields(row)
            connection.execute(
                "INSERT INTO country_inference_items(run_id,posting_id,"
                "listing_revision_id,content_revision_id,listing_hash,content_hash,"
                "input_hash,status,reason) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    row["posting_id"],
                    row["listing_revision_id"],
                    row["content_revision_id"],
                    row["listing_hash"],
                    row["content_hash"],
                    _input_hash(row),
                    "unknown" if reason else "pending",
                    reason,
                ),
            )
    return run_id


_HALTING = frozenset(
    {"auth", "budget_stopped", "ledger", "internal", "protocol", "connection"}
)


def _persist(
    connection: sqlite3.Connection, run_id: str, posting_id: str, result: dict[str, Any]
) -> None:
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            _SOURCE + " WHERE p.posting_id=?", (posting_id,)
        ).fetchone()
        item = connection.execute(
            "SELECT input_hash FROM country_inference_items "
            "WHERE run_id=? AND posting_id=?",
            (run_id, posting_id),
        ).fetchone()
        if row is None or item is None or _input_hash(row) != item[0]:
            result = {"status": "stale"}
        connection.execute(
            "UPDATE country_inference_items SET status=?,country=?,confidence=?,"
            "reason=?,evidence_json=?,origin_run_id=? "
            "WHERE run_id=? AND posting_id=? AND status='running'",
            (
                result["status"],
                result.get("country"),
                result.get("confidence"),
                result.get("reason"),
                json.dumps(result.get("evidence", [])),
                run_id,
                run_id,
                posting_id,
            ),
        )


def run_country(
    connection: sqlite3.Connection,
    run_id: str,
    *,
    session_factory: Any = None,
) -> str:
    """Process pending items of one run with one sidecar; returns final status."""
    path = Path(connection.execute("PRAGMA database_list").fetchone()[2])
    ai = active_config(path)
    owner = str(uuid4())
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        claimed = connection.execute(
            "UPDATE country_inference_runs SET owner=?,lease_until_ms=?,updated_at=? "
            "WHERE run_id=? AND status='running' AND configuration_hash=?",
            (
                owner,
                int(time.time() * 1000) + LEASE_MS,
                _now(),
                run_id,
                run_configuration(ai)[1],
            ),
        ).rowcount
    if claimed != 1:
        raise CountryError("Country run cannot be claimed with this configuration.")
    ledger = AttemptLedger(path)
    ledger.create_run(run_id, run_limits(ai))
    ledger.require_resumable(run_id)
    require_ready(path)
    lock = threading.Lock()
    fatal = threading.Event()

    def claim() -> tuple[str, dict[str, str]] | None:
        with lock, connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                "SELECT owner,stop_requested FROM country_inference_runs "
                "WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if run is None or run[0] != owner or run[1] or fatal.is_set():
                return None
            connection.execute(
                "UPDATE country_inference_runs SET lease_until_ms=?,updated_at=? "
                "WHERE run_id=?",
                (int(time.time() * 1000) + LEASE_MS, _now(), run_id),
            )
            item = connection.execute(
                "SELECT posting_id,input_hash FROM country_inference_items "
                "WHERE run_id=? AND status='pending' "
                "ORDER BY CAST(posting_id AS INTEGER),posting_id LIMIT 1",
                (run_id,),
            ).fetchone()
            if item is None:
                return None
            source = connection.execute(
                _SOURCE + " WHERE p.posting_id=?", (item[0],)
            ).fetchone()
            if source is None or _input_hash(source) != item[1]:
                connection.execute(
                    "UPDATE country_inference_items SET status='stale' "
                    "WHERE run_id=? AND posting_id=?",
                    (run_id, item[0]),
                )
                return claim_again
            connection.execute(
                "UPDATE country_inference_items SET status='running',attempts=1 "
                "WHERE run_id=? AND posting_id=?",
                (run_id, item[0]),
            )
            return item[0], _fields(source)[0]

    claim_again: tuple[str, dict[str, str]] = ("", {})

    def worker(session: PiSession) -> None:
        while True:
            claimed_item = claim()
            if claimed_item is None:
                return
            if claimed_item is claim_again:
                continue
            posting_id, fields = claimed_item
            user = (
                json.dumps(
                    {"fields": fields}, ensure_ascii=False, separators=(",", ":")
                )
                .replace("<", r"\u003c")
                .replace(">", r"\u003e")
            )
            outcome = session.run_item(
                PiItem(
                    item_id=f"c-{posting_id}",
                    kind="country",
                    system=f"{PROMPT.strip()} {COUNTRY_TOOL_INSTRUCTIONS}",
                    user=user,
                    sections=tuple(fields.items()),
                    submit_schema=country_schema(fields),
                    operation_key=f"country|{posting_id}|{_hash(user)}",
                    check=_checker(fields),
                )
            )
            if outcome.status == "submitted" and outcome.payload is not None:
                try:
                    result = validate_country(outcome.payload, fields)
                except ValueError as error:
                    result = {"status": "failed", "reason": str(error)}
            else:
                category = outcome.category or "internal"
                if category in _HALTING or (
                    not ai.is_zero_cost and category == "timeout"
                ):
                    fatal.set()
                result = {
                    "status": "failed",
                    "reason": "invalid_output"
                    if category
                    in (
                        "evidence_rejected",
                        "no_submission",
                        "max_turns",
                        "max_tool_calls",
                    )
                    else "infrastructure",
                }
            with lock:
                _persist(connection, run_id, posting_id, result)

    endpoint = (
        validate_base_url(ai.custom_base_url or "")
        if ai.access_mode == "custom"
        else None
    )
    factory = session_factory or (
        lambda: PiSession(path, ai, run_id, endpoint=endpoint).start()
    )
    session = factory()
    try:
        with concurrent.futures.ThreadPoolExecutor(ai.parallel_streams) as pool:
            for future in [
                pool.submit(worker, session) for _ in range(ai.parallel_streams)
            ]:
                future.result()
    finally:
        session.close()
    with lock, connection:
        connection.execute("BEGIN IMMEDIATE")
        counts = dict(
            connection.execute(
                "SELECT status,count(*) FROM country_inference_items "
                "WHERE run_id=? GROUP BY status",
                (run_id,),
            ).fetchall()
        )
        status = (
            "failed"
            if fatal.is_set() or counts.get("failed")
            else "stopped"
            if counts.get("pending") or counts.get("running")
            else "completed"
        )
        connection.execute(
            "UPDATE country_inference_items SET status='failed',reason='interrupted' "
            "WHERE run_id=? AND status='running'",
            (run_id,),
        )
        connection.execute(
            "UPDATE country_inference_runs SET status=?,owner=NULL,lease_until_ms=0,"
            "updated_at=? WHERE run_id=? AND owner=?",
            (status, _now(), run_id, owner),
        )
    return status


def _checker(
    fields: dict[str, str],
) -> Callable[[dict[str, Any]], tuple[bool, str | None]]:
    return lambda payload: _check(payload, fields)


def _check(payload: dict[str, Any], fields: dict[str, str]) -> tuple[bool, str | None]:
    try:
        validate_country(payload, fields)
        return True, None
    except ValueError:
        return False, (
            "Rejected: quotes must be exact unique substrings of the named field, "
            "never headquarters/eligibility/pay clauses; unknown answers need "
            "country=null, confidence=null and no evidence."
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Explicit work-country predictions with the active Pi AI "
        "configuration (bounded; API-key mode may be paid)"
    )
    parser.add_argument("database", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start")
    selection = start.add_mutually_exclusive_group(required=True)
    selection.add_argument("--posting-id", action="append")
    selection.add_argument("--all", action="store_true", dest="all_postings")
    start.add_argument("--limit", type=int, default=10_000)
    stop = commands.add_parser("stop")
    stop.add_argument("run_id")
    args = parser.parse_args()
    if args.command == "start" and not 1 <= args.limit <= 10_000:
        parser.error("--limit must be 1..10000")
    from waterlooworks_flash_next.runtime_secrets import load_local_environment

    # Explicit operator CLI only; values stay in the private lookup.
    load_local_environment(Path(__file__).resolve().parents[3] / ".env")
    connection = open_database(args.database)
    try:
        if args.command == "stop":
            with connection:
                connection.execute(
                    "UPDATE country_inference_runs SET stop_requested=1 "
                    "WHERE run_id=? AND status='running'",
                    (args.run_id,),
                )
            return
        with ProviderLease(args.database):
            ai = active_config(args.database)
            run_id = begin_run(
                connection,
                ai,
                None if args.all_postings else list(args.posting_id),
                args.limit,
            )
            print(json.dumps({"run_id": run_id, "status": "running"}), flush=True)
            status = run_country(connection, run_id)
            print(json.dumps({"run_id": run_id, "status": status}), flush=True)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
