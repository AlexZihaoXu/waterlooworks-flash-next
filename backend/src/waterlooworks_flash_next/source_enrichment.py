"""Read saved extraction checkpoints, never execute extraction or change storage."""

import hashlib
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from waterlooworks_flash_next.api_models import (
    CompensationRate,
    ExtractedKeyword,
    ExtractionState,
    ExtractionStatus,
)
from waterlooworks_flash_next.compensation_parser import PayAmount
from waterlooworks_flash_next.content import StoredPostingContent
from waterlooworks_flash_next.inference_reuse import compatible_origin


@dataclass
class SourcePay:
    rate: CompensationRate
    start: int
    end: int
    anchor_certain: bool = True


@dataclass
class SourceEnrichment:
    salary: ExtractionStatus = field(default_factory=ExtractionStatus)
    keywords_status: ExtractionStatus = field(default_factory=ExtractionStatus)
    pay: list[SourcePay] = field(default_factory=list)
    keywords: list[ExtractedKeyword] = field(default_factory=list)


def valid_span(source: str, start: int, end: int) -> str | None:
    return source[start:end] if 0 <= start < end <= len(source) else None


def extraction_state(statuses: list[str], *, usable: int) -> ExtractionState:
    if not statuses:
        return "not_requested"
    if "stale" in statuses:
        return "stale"
    if any(s in ("pending", "running") for s in statuses):
        return "pending"
    if all(s == "accepted" for s in statuses):
        return "accepted" if usable else "empty"
    if usable or "partial" in statuses or "accepted" in statuses:
        return "partial"
    return "failed" if "failed" in statuses else "rejected"


def read_source_enrichment(
    connection: sqlite3.Connection, posting_id: str | None = None
) -> dict[str, SourceEnrichment]:
    """Fixed number of bulk queries; caller owns a consistent read transaction.

    Select the latest intended run per mode, not the latest successful result per
    posting. Cache entries can originate in an older run, but all source/config
    identities must match the selected item. Pending modes expose no children.
    """
    result: dict[str, SourceEnrichment] = {}
    for mode in ("salary", "keywords"):
        run = connection.execute(
            "SELECT r.* FROM source_extraction_runs r WHERE EXISTS "
            "(SELECT 1 FROM source_extraction_items i WHERE i.run_id=r.run_id "
            "AND i.mode=?) ORDER BY created_at DESC,run_id DESC LIMIT 1",
            (mode,),
        ).fetchone()
        if run is None:
            continue
        config_valid = (
            hashlib.sha256(run["configuration_json"].encode()).hexdigest()
            == run["configuration_hash"]
        )
        restriction = " AND i.posting_id=?" if posting_id is not None else ""
        parameters = (run["run_id"], mode) + (
            (posting_id,) if posting_id is not None else ()
        )
        rows = connection.execute(
            "SELECT i.*,c.content_hash current_hash,c.content_json,"
            "e.extraction_id verified_id,ir.revision_id verified_revision, "
            "origin.run_id origin_run_id, e.content_revision_id origin_revision_id, "
            "origin.configuration_json origin_configuration_json, "
            "origin.configuration_hash origin_configuration_hash "
            "FROM source_extraction_items i "
            "LEFT JOIN posting_contents c ON c.posting_id=i.posting_id "
            "LEFT JOIN source_extractions_current e ON e.extraction_id=i.extraction_id "
            "AND e.posting_id=i.posting_id AND e.content_hash=i.content_hash "
            "AND e.mode=i.mode AND e.part=i.part "
            "AND e.rejected_count=i.rejected_count "
            "AND EXISTS (SELECT 1 FROM posting_content_revisions er "
            "WHERE er.revision_id=e.content_revision_id AND er.posting_id=e.posting_id "
            "AND er.content_hash=e.content_hash) "
            "LEFT JOIN source_extraction_runs origin ON origin.run_id=e.run_id "
            "AND origin.configuration_hash=e.configuration_hash "
            "LEFT JOIN posting_content_revisions ir "
            "ON ir.revision_id=i.content_revision_id "
            "AND ir.posting_id=i.posting_id AND ir.content_hash=i.content_hash "
            "WHERE i.run_id=? AND i.mode=?"
            + restriction
            + " ORDER BY i.posting_id,i.part",
            parameters,
        ).fetchall()
        grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            grouped[row["posting_id"]].append(row)
        children: dict[int, list[sqlite3.Row]] = defaultdict(list)
        table = (
            "source_extraction_pay" if mode == "salary" else "source_extraction_terms"
        )
        for child in connection.execute(
            f"SELECT child.* FROM {table} child JOIN source_extraction_items i "
            "ON i.extraction_id=child.extraction_id WHERE i.run_id=? AND i.mode=?"
            + restriction,
            parameters,
        ):
            children[child["extraction_id"]].append(child)
        for id_, items in grouped.items():
            output = result.setdefault(id_, SourceEnrichment())
            statuses = [
                "stale"
                if not config_valid
                or i["content_hash"] != i["current_hash"]
                or i["verified_revision"] is None
                or (
                    i["status"] in ("accepted", "partial")
                    and (
                        i["verified_id"] is None
                        or (
                            i["origin_configuration_hash"] != run["configuration_hash"]
                            and i["origin_revision_id"] != i["content_revision_id"]
                        )
                        or not compatible_origin(
                            i["origin_configuration_json"],
                            i["origin_configuration_hash"],
                            run["configuration_json"],
                            run["configuration_hash"],
                            "extraction",
                        )
                    )
                )
                else "partial"
                if i["status"] == "accepted" and i["rejected_count"]
                else i["status"]
                for i in items
            ]
            state = extraction_state(statuses, usable=0)
            invalid = False
            if state not in ("stale", "pending"):
                content = StoredPostingContent.model_validate_json(
                    items[0]["content_json"]
                )
                for item in items:
                    if item["status"] not in ("accepted", "partial"):
                        continue
                    for child in children[item["extraction_id"]]:
                        source = content.fields.get(child["field"], "")
                        if mode == "keywords":
                            text = valid_span(source, child["start"], child["end"])
                            if (
                                text is None
                                or " ".join(text.split()).lower() != child["name"]
                            ):
                                invalid = True
                                continue
                            output.keywords.append(
                                ExtractedKeyword(
                                    name=child["name"],
                                    source_field=child["field"],
                                    evidence_text=text,
                                )
                            )
                        else:
                            text = valid_span(
                                source, child["quote_start"], child["quote_end"]
                            )
                            if text is None or not (
                                child["quote_start"]
                                <= child["amount_start"]
                                < child["amount_end"]
                                <= child["quote_end"]
                            ):
                                invalid = True
                                continue
                            values = {
                                key: child[key]
                                for key in (
                                    "bound_type",
                                    "minimum",
                                    "maximum",
                                    "minimum_inclusive",
                                    "maximum_inclusive",
                                    "period",
                                )
                            }
                            if any(
                                child[key] is not None
                                and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", child[key])
                                is None
                                for key in ("minimum", "maximum")
                            ):
                                invalid = True
                                continue
                            try:
                                PayAmount.model_validate(values, strict=False)
                                rate = CompensationRate.model_validate(
                                    dict(
                                        values,
                                        currency=child["currency"],
                                        hours_per_week=None,
                                        payout_frequency=None,
                                        eligibility=None,
                                        source_field=child["field"],
                                        evidence_text=text,
                                    )
                                )
                            except (ValidationError, InvalidOperation):
                                invalid = True
                                continue
                            refined = refine_pay_evidence(
                                rate,
                                child["amount_start"] - child["quote_start"],
                                child["amount_end"] - child["quote_start"],
                            )
                            if refined is None:
                                invalid = True
                                continue
                            output.pay.append(
                                SourcePay(
                                    refined, child["amount_start"], child["amount_end"]
                                )
                            )
                output.keywords = list({k.name: k for k in output.keywords}.values())
                output.keywords.sort(key=lambda k: k.name)
                state = extraction_state(
                    statuses,
                    usable=len(output.pay if mode == "salary" else output.keywords),
                )
                if invalid:
                    state = "partial"
            status = ExtractionStatus(
                state=state,
                run_id=run["run_id"],
                configuration_hash=run["configuration_hash"],
                origin_run_ids=sorted(
                    {
                        item["origin_run_id"]
                        for item, item_state in zip(items, statuses, strict=True)
                        if item_state in ("accepted", "partial")
                        and item["origin_run_id"] is not None
                    }
                ),
                reused_slices=sum(
                    item_state in ("accepted", "partial")
                    and item["origin_run_id"] != run["run_id"]
                    for item, item_state in zip(items, statuses, strict=True)
                ),
                accepted_slices=sum(s in ("accepted", "partial") for s in statuses),
                total_slices=len(items),
            )
            if mode == "salary":
                output.salary = status
            else:
                output.keywords_status = status
    return result


_CODES = r"\b(?:CAD|USD|EUR|GBP|AUD|NZD|CHF|JPY|CNY|HKD|SGD|INR)\b"
_HOURS = re.compile(
    r"(?<![\w.,])(?P<hours>\d+(?:\.\d+|,\d{1,2})?)\s*[-/]?\s*(?:hours?|hrs?|h)"
    r"\s*(?:work[\s-]*week|(?:/|per)\s*(?:week|wk))\b",
    re.IGNORECASE,
)


def refine_pay_evidence(
    rate: CompensationRate, start: int, end: int
) -> CompensationRate | None:
    """Recover adjacent explicit units; exclude clearly anchored work-week hours.

    Offsets are relative to the exact saved quote. Neither source records nor
    amounts are modified. Bare dollar signs do not establish a currency.
    """
    text = rate.evidence_text
    schedules = [
        match
        for match in _HOURS.finditer(text)
        if not re.search(r"[$€£]\s*$", text[: match.start()])
        and not re.search(_CODES + r"\s*$", text[: match.start()], re.IGNORECASE)
    ]
    if any(start <= m.start("hours") < end for m in schedules):
        return None
    updates: dict[str, object] = {}
    if len(schedules) == 1 and rate.hours_per_week is None:
        hours = schedules[0]["hours"].replace(",", ".")
        if 0 < Decimal(hours) <= 168:
            updates["hours_per_week"] = hours
    codes = {m.upper() for m in re.findall(_CODES, text, re.IGNORECASE)}
    if len(codes) == 1:
        adjacent = re.search(_CODES, text[start:end], re.IGNORECASE)
        prefix = re.search(_CODES + r"[\s$]*$", text[:start], re.IGNORECASE)
        suffix = re.match(
            r"[\s/().-]*(?:(?:per|a|an)\s+)?"
            r"(?:(?:hours?|hrs?|h|days?|weeks?|months?|mo|years?|yr|annum)"
            r"\b[\s/().-]*)?" + _CODES,
            text[end:],
            re.IGNORECASE,
        )
        if adjacent or prefix or suffix:
            currency = next(iter(codes))
            if rate.currency is not None and rate.currency != currency:
                return None
            if rate.currency is None:
                updates["currency"] = currency
    return rate.model_copy(update=updates) if updates else rate


_CORE = ("bound_type", "minimum", "maximum", "minimum_inclusive", "maximum_inclusive")


def compatible_pay(left: CompensationRate, right: CompensationRate) -> bool:
    def value(rate: CompensationRate, key: str) -> object:
        raw = getattr(rate, key)
        return (
            Decimal(raw) if key in ("minimum", "maximum") and raw is not None else raw
        )

    return all(value(left, key) == value(right, key) for key in _CORE) and all(
        getattr(left, key) is None
        or getattr(right, key) is None
        or getattr(left, key) == getattr(right, key)
        for key in (
            "currency",
            "period",
            "hours_per_week",
            "payout_frequency",
            "eligibility",
        )
    )


def legacy_amount_span(
    rate: CompensationRate, start: int, end: int
) -> tuple[int, int, bool]:
    """Locate unique numeric bounds inside a possibly broad legacy evidence quote.

    Ambiguous repeated bounds retain the broad span: reconciliation then fails
    closed instead of claiming that adjacent tiers are the same rate clause.
    """
    spans = []
    for value in dict.fromkeys(
        v for v in (rate.minimum, rate.maximum) if v is not None
    ):
        matches = []
        for match in re.finditer(
            r"(?<![\w.])[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?[kK]?(?![\w.])",
            rate.evidence_text,
        ):
            token = match[0].replace(",", "")
            amount = Decimal(token.rstrip("kK")) * (
                1000 if token[-1:].lower() == "k" else 1
            )
            if amount == Decimal(value):
                matches.append((start + match.start(), start + match.end()))
        if len(matches) != 1:
            return start, end, False
        spans.extend(matches)
    return (
        (min(s for s, _ in spans), max(e for _, e in spans), True)
        if spans
        else (start, end, False)
    )


def merge_compensation(
    legacy: list[SourcePay], extracted: list[SourcePay]
) -> tuple[list[CompensationRate], bool]:
    """Reconcile rate clauses, not quote overlap; disputed clauses are excluded.

    Compatible source-current legacy metadata survives. No missing hours,
    eligibility, payout, period or currency is guessed. Different amount spans
    remain distinct tiers even when they share a broad source quote.
    """
    result = [
        SourcePay(old.rate, old.start, old.end, old.anchor_certain) for old in legacy
    ]
    disputed: list[SourcePay] = []
    for saved in extracted:
        new = SourcePay(saved.rate, saved.start, saved.end, saved.anchor_certain)
        overlapping = [
            old
            for old in result
            if old.rate.source_field == new.rate.source_field
            and old.start < new.end
            and new.start < old.end
        ]
        if any(
            old.rate.source_field == new.rate.source_field
            and old.start < new.end
            and new.start < old.end
            for old in disputed
        ):
            disputed.append(new)
            continue
        if any(
            not old.anchor_certain
            or not new.anchor_certain
            or not compatible_pay(old.rate, new.rate)
            for old in overlapping
        ):
            disputed.extend([new, *overlapping])
            result = [old for old in result if old not in overlapping]
            continue
        if overlapping:
            for old in overlapping:
                updates = {
                    key: getattr(new.rate, key)
                    for key in ("currency", "period")
                    if getattr(old.rate, key) is None
                    and getattr(new.rate, key) is not None
                }
                if updates:
                    # Include the new exact quote when it supplies a source unit.
                    updates["evidence_text"] = new.rate.evidence_text
                    old.rate = old.rate.model_copy(update=updates)
        else:
            result.append(new)
    return [entry.rate for entry in result], bool(disputed)
