"""Bulk source-current country predictions; never infer on reads."""

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from waterlooworks_flash_next.api_models import CountryEvidence, CountryInference
from waterlooworks_flash_next.country_schema import COUNTRIES, COUNTRY_VERSION
from waterlooworks_flash_next.inference_reuse import (
    compatible_origin,
    configuration_valid,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _configuration_valid(config: dict[str, Any], raw: str, digest: str) -> bool:
    if config.get("provider") == "openai-responses":
        return configuration_valid(raw, digest, "country")
    model = config.get("model", {})
    return (
        _hash(raw) == digest
        and config.get("version") == COUNTRY_VERSION
        and config.get("countries_hash")
        == _hash(json.dumps(COUNTRIES, ensure_ascii=False, separators=(",", ":")))
        and config.get("prompt_hash")
        == _hash(
            Path(__file__).with_name("country-prompt.txt").read_text(encoding="utf-8")
        )
        and model.get("id") == "unsloth/gemma-4-26b-a4b-it"
        and model.get("quantization", {}).get("name") == "Q2_K_XL"
        and model.get("config", {}).get("context_length") == 65536
        and model.get("config", {}).get("parallel") == 4
        and config.get("reasoning") == "none"
    )


def read_country_inferences(
    connection: sqlite3.Connection, posting_id: str | None = None
) -> dict[str, CountryInference]:
    """Latest intended run per posting, never fall back to an older success.

    Both saved inputs (including absent content) and immutable revision identities
    must match. One bulk query, no per-card database access. Caller owns snapshot.
    """
    result: dict[str, CountryInference] = {}
    restriction = " AND i.posting_id=?" if posting_id is not None else ""
    rows = connection.execute(
        "SELECT i.*,r.configuration_hash,r.configuration_json,p.listing_json,"
        "p.content_hash current_listing_hash,c.content_json,"
        "c.content_hash current_content_hash,"
        "lr.content_hash revision_listing_hash,cr.content_hash revision_content_hash, "
        "origin.configuration_json origin_configuration_json, "
        "origin.configuration_hash origin_configuration_hash, "
        "original.run_id verified_origin "
        "FROM country_inference_items i JOIN country_inference_runs r USING(run_id) "
        "JOIN postings p USING(posting_id) "
        "LEFT JOIN posting_contents c USING(posting_id) "
        "LEFT JOIN posting_revisions lr ON lr.revision_id=i.listing_revision_id "
        "AND lr.posting_id=i.posting_id "
        "LEFT JOIN posting_content_revisions cr "
        "ON cr.revision_id=i.content_revision_id "
        "AND cr.posting_id=i.posting_id "
        "LEFT JOIN country_inference_items original ON original.run_id=i.origin_run_id "
        "AND original.posting_id=i.posting_id AND original.status=i.status "
        "AND original.status IN ('accepted','unknown') "
        "AND original.country IS i.country AND original.confidence IS i.confidence "
        "AND original.reason IS i.reason AND original.evidence_json=i.evidence_json "
        "AND original.input_hash=i.input_hash "
        "AND original.listing_revision_id=i.listing_revision_id "
        "AND original.content_revision_id IS i.content_revision_id "
        "AND original.listing_hash=i.listing_hash "
        "AND original.content_hash IS i.content_hash "
        "LEFT JOIN country_inference_runs origin ON origin.run_id=original.run_id "
        "WHERE NOT EXISTS (SELECT 1 FROM country_inference_items newer "
        "JOIN country_inference_runs nr ON nr.run_id=newer.run_id "
        "WHERE newer.posting_id=i.posting_id "
        "AND (nr.created_at,nr.run_id)>(r.created_at,r.run_id))" + restriction,
        (posting_id,) if posting_id is not None else (),
    )
    configs: dict[str, tuple[dict[str, Any], bool]] = {}
    for row in rows:
        output = CountryInference(
            run_id=row["run_id"],
            origin_run_id=row["origin_run_id"],
            configuration_hash=row["configuration_hash"],
        )
        result[row["posting_id"]] = output
        try:
            if row["run_id"] not in configs:
                config = json.loads(row["configuration_json"])
                configs[row["run_id"]] = (
                    config,
                    _configuration_valid(
                        config, row["configuration_json"], row["configuration_hash"]
                    ),
                )
            config, valid = configs[row["run_id"]]
            if (
                not valid
                or row["input_hash"]
                != _hash(
                    json.dumps(
                        [row["listing_hash"], row["content_hash"]],
                        separators=(",", ":"),
                    )
                )
                or row["listing_hash"] != row["current_listing_hash"]
                or row["content_hash"] != row["current_content_hash"]
                or row["listing_hash"] != row["revision_listing_hash"]
                or row["content_hash"] != row["revision_content_hash"]
            ):
                output.state = "stale"
                continue
            if (
                row["origin_run_id"] is not None
                and row["origin_run_id"] != row["run_id"]
            ):
                if row["verified_origin"] is None or not compatible_origin(
                    row["origin_configuration_json"],
                    row["origin_configuration_hash"],
                    row["configuration_json"],
                    row["configuration_hash"],
                    "country",
                ):
                    output.state = "stale"
                    continue
                config = json.loads(row["origin_configuration_json"])
            output.model_id = config["model"]["id"]
            output.prompt_version = config["version"]
            state = row["status"]
            output.state = "pending" if state == "running" else state
            output.reason = row["reason"]
            if state != "accepted":
                continue
            code = row["country"]
            if code not in COUNTRIES or not 0.85 <= row["confidence"] <= 1:
                raise ValueError("Invalid prediction")
            listing = json.loads(row["listing_json"])
            content = (
                json.loads(row["content_json"])["fields"] if row["content_json"] else {}
            )
            evidence = []
            for entry in json.loads(row["evidence_json"]):
                kind, field = entry["field"].split(":", 1)
                if kind not in ("listing", "content"):
                    raise ValueError("Invalid source kind")
                source = (listing if kind == "listing" else content).get(field)
                start, end = entry["start"], entry["end"]
                if (
                    not isinstance(source, str)
                    or type(start) is not int
                    or type(end) is not int
                    or not 0 <= start < end <= len(source)
                    or end - start > 500
                    or _hash(source[start:end]) != entry["quote_hash"]
                ):
                    raise ValueError("Invalid location context")
                evidence.append(
                    CountryEvidence(
                        source_kind=kind,
                        source_field=field,
                        start=start,
                        end=end,
                        text=source[start:end],
                    )
                )
            if not 1 <= len(evidence) <= 3:
                raise ValueError("Missing location context")
            output.country_code = code
            output.country_name = COUNTRIES[code]
            output.confidence = row["confidence"]
            output.evidence = evidence
        except (ValueError, TypeError, KeyError, AttributeError):
            output.state = "stale"
            output.country_code = output.country_name = None
            output.confidence = None
            output.evidence = []
    return result
