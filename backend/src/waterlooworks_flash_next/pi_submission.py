"""Submission resolution and fixed-code feedback for Pi extraction.

Models may cite evidence by reference (`{field, start, end}` offsets from
read_section/find_span) instead of retyping it, or quote text that differs from
the source only by whitespace, NBSP, smart quotes, dashes or case. Python
reconstructs the exact source substring before the authoritative
`validate_extraction` runs; nothing here relaxes validation itself.

Every rejection is a fixed reason code plus a template that may name schema
paths and section (field) names only — never source text or model output.
"""

import re
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from waterlooworks_flash_next.model_validation import (
    ExtractionCandidate,
    ModelOutputError,
    ValidatedExtraction,
    _validate_rate,
    _validate_skill,
    validate_extraction,
)

MAX_EVIDENCE_CHARS = 4096
# Code points: spaces (NBSP, figure, narrow NBSP), single/double smart quotes,
# hyphens/dashes and minus sign.
_TRANSLATE = str.maketrans(
    {0x00A0: " ", 0x2007: " ", 0x202F: " "}
    | dict.fromkeys((0x2018, 0x2019, 0x201A, 0x201B), "'")
    | dict.fromkeys((0x201C, 0x201D, 0x201E), '"')
    | dict.fromkeys((0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212), "-")
)

# validate_extraction's fixed messages -> stable reason codes.
_MESSAGE_CODES = {
    "Model evidence field is unsupported.": "evidence_field_unsupported",
    "Model evidence is not a unique exact source span.": "evidence_not_unique",
    "Model rate evidence splits an original numeric token.": "evidence_splits_number",
    "Model rate is not supported as base pay.": "rate_not_base_pay",
    "Model fixed bounds are inconsistent.": "rate_fixed_bounds",
    "Model range bounds are inconsistent.": "rate_range_bounds",
    "Model minimum bound is not source supported.": "rate_minimum_unsupported",
    "Model maximum bound is not source supported.": "rate_maximum_unsupported",
    "Model rate amount is not source supported.": "rate_amount_unsupported",
    "Model fixed rate is not an explicit fixed pay amount.": "rate_fixed_not_explicit",
    "Model range is not an explicit source range.": "rate_range_not_explicit",
    "Model open bound is not tied to its source amount.": "rate_open_bound",
    "Model currency is not explicitly source supported.": "rate_currency_unsupported",
    "Model period is not explicitly source supported.": "rate_period_unsupported",
    "Model payout frequency is not source supported.": "rate_payout_unsupported",
    "Model eligibility is not exact source evidence.": "rate_eligibility_not_exact",
    "Model amount is not canonical decimal text.": "rate_amount_not_decimal",
    "Model amount is not a supported Decimal.": "rate_amount_not_decimal",
    "Model skill name is not the exact grounded mention.": "skill_name_not_grounded",
    "Model skill mention is negated or optional.": "skill_negated",
    "Model skill qualifier is not in the evidence field.": "skill_qualifier_field",
    "Model skill requirement is not source supported.": "skill_requirement_unsupported",
    "Model extraction shape is invalid.": "schema",
    "Model returned an unrequested extraction category.": "unrequested_category",
    "Multiple model rates require exact tier eligibility.": "rates_need_eligibility",
    "Model returned duplicate compensation rates.": "duplicate_rates",
    "Model returned duplicate deterministic skill output.": "duplicate_skill",
}

_HINTS = {
    "evidence_not_found": "{path}: evidence text was not found in section "
    "{section}. Cite {{field,start,end}} offsets from find_span/read_section, or "
    "copy the words exactly.",
    "evidence_offsets_invalid": "{path}: start/end offsets are outside section "
    "{section} or longer than 4096 characters.",
    "evidence_missing": "{path}: give either text or start/end offsets.",
    "evidence_field_unsupported": "{path}: field must be one of the listed "
    "sections (skills only from Required Skills, Job Responsibilities or Job "
    "Summary).",
    "evidence_not_unique": "{path}: the evidence occurs more than once in "
    "section {section}; include a few more surrounding words or exact offsets.",
    "evidence_splits_number": "{path}: evidence cuts through a number; cite the "
    "whole amount.",
    "rate_not_base_pay": "{path}: this is not base pay (benefit, stipend, bonus, "
    "equivalent or conversion). Omit it.",
    "rate_fixed_bounds": "{path}: fixed pay needs minimum == maximum and both "
    "inclusive flags true.",
    "rate_range_bounds": "{path}: range needs minimum < maximum with inclusive "
    "flags set.",
    "rate_minimum_unsupported": "{path}: minimum is not the amount in the evidence.",
    "rate_maximum_unsupported": "{path}: maximum is not the amount in the evidence.",
    "rate_amount_unsupported": "{path}: amount is not stated in the evidence.",
    "rate_fixed_not_explicit": "{path}: the evidence does not state one fixed "
    "amount; use range/minimum/maximum or omit.",
    "rate_range_not_explicit": "{path}: the evidence does not state a range.",
    "rate_open_bound": "{path}: an open bound must quote its own amount.",
    "rate_currency_unsupported": "{path}: currency must be null unless an ISO "
    "code or currency name is in the evidence ($ alone is null).",
    "rate_period_unsupported": "{path}: period must be null unless the evidence "
    "states it (hour/day/week/month/year).",
    "rate_payout_unsupported": "{path}: payout_frequency must be null unless "
    "stated in the evidence.",
    "rate_eligibility_not_exact": "{path}: eligibility must equal "
    "eligibility_evidence text from the same pay clause, or both null.",
    "rate_amount_not_decimal": "{path}: amounts are plain decimal strings like "
    '"25" or "25.5" (expand k to thousands).',
    "skill_name_not_grounded": "{path}: skill must be the exact mention inside "
    "its evidence.",
    "skill_negated": "{path}: negated or optional skills must be omitted.",
    "skill_qualifier_field": "{path}: requirement_evidence must be in the same "
    "section as the skill evidence.",
    "skill_requirement_unsupported": "{path}: required/preferred needs an exact "
    "marker word in requirement_evidence; otherwise unspecified with null.",
    "unrequested_category": "{path}: this category was not requested; return an "
    "empty list.",
    "rates_need_eligibility": "base_rates: several rates each need exact "
    "eligibility evidence; otherwise submit one rate.",
    "duplicate_rates": "base_rates: the same rate is listed twice.",
    "duplicate_skill": "skills: a skill is repeated or already in "
    "deterministic_skills.",
    "schema": "{path}: value does not match the required schema.",
}


class SubmissionRejected(Exception):
    """Fixed reason code and template-only hint; never contains source text."""

    def __init__(self, code: str, path: str = "submission", section: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.path = path
        self.section = section

    @property
    def hint(self) -> str:
        template = _HINTS.get(self.code, "{path}: submission rejected ({code}).")
        return (
            "Rejected ["
            + self.code
            + "]: "
            + template.format(
                path=self.path, section=repr(self.section), code=self.code
            )
        )


def normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Casefold, unify quotes/dashes/spaces and collapse whitespace runs.

    Returns the normalized text and, per normalized character, its source index.
    """
    out: list[str] = []
    index: list[int] = []
    previous_space = False
    for position, character in enumerate(text):
        mapped = character.translate(_TRANSLATE)
        if mapped.isspace():
            if previous_space:
                continue
            previous_space = True
            out.append(" ")
            index.append(position)
            continue
        previous_space = False
        for folded in mapped.casefold():
            out.append(folded)
            index.append(position)
    return "".join(out), index


def _locate(source: str, text: str) -> tuple[int, int] | None:
    """Exact span of `text` in `source`, falling back to a unique normalized match."""
    start = source.find(text)
    if start >= 0:
        return start, start + len(text)
    needle, _ = normalize_with_map(text.strip())
    if not needle:
        return None
    haystack, index = normalize_with_map(source)
    found = haystack.find(needle)
    if found < 0 or haystack.find(needle, found + 1) >= 0:
        return None
    return index[found], index[found + len(needle) - 1] + 1


def _resolve_evidence(value: Any, fields: dict[str, str], path: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise SubmissionRejected("schema", path)
    field = value.get("field")
    if not isinstance(field, str) or field not in fields:
        raise SubmissionRejected("evidence_field_unsupported", path)
    source = fields[field]
    start, end, text = value.get("start"), value.get("end"), value.get("text")
    if start is not None or end is not None:
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or not 0 <= start < end <= len(source)
            or end - start > MAX_EVIDENCE_CHARS
        ):
            raise SubmissionRejected("evidence_offsets_invalid", path, field)
        return {"field": field, "text": source[start:end]}
    if not isinstance(text, str) or not text.strip():
        raise SubmissionRejected("evidence_missing", path, field)
    span = _locate(source, text)
    if span is None:
        raise SubmissionRejected("evidence_not_found", path, field)
    return {"field": field, "text": source[span[0] : span[1]]}


def resolve_payload(payload: dict[str, Any], fields: dict[str, str]) -> dict[str, Any]:
    """Map referenced/approximate evidence to exact source text (shape-preserving)."""
    if not isinstance(payload, dict) or set(payload) != {"base_rates", "skills"}:
        raise SubmissionRejected("schema", "submission")
    rates, skills = payload["base_rates"], payload["skills"]
    if not isinstance(rates, list) or not isinstance(skills, list):
        raise SubmissionRejected("schema", "submission")
    resolved_rates = []
    for number, rate in enumerate(rates):
        path = f"base_rates[{number}]"
        if not isinstance(rate, dict):
            raise SubmissionRejected("schema", path)
        rate = dict(rate)
        rate["evidence"] = _resolve_evidence(
            rate.get("evidence"), fields, path + ".evidence"
        )
        if rate.get("eligibility_evidence") is not None:
            rate["eligibility_evidence"] = _resolve_evidence(
                rate["eligibility_evidence"], fields, path + ".eligibility_evidence"
            )
            # The eligibility label must equal its evidence text exactly.
            rate["eligibility"] = rate["eligibility_evidence"]["text"]
        resolved_rates.append(rate)
    resolved_skills = []
    for number, skill in enumerate(skills):
        path = f"skills[{number}]"
        if not isinstance(skill, dict):
            raise SubmissionRejected("schema", path)
        skill = dict(skill)
        skill["evidence"] = _resolve_evidence(
            skill.get("evidence"), fields, path + ".evidence"
        )
        if skill.get("requirement_evidence") is not None:
            skill["requirement_evidence"] = _resolve_evidence(
                skill["requirement_evidence"], fields, path + ".requirement_evidence"
            )
        resolved_skills.append(skill)
    return {"base_rates": resolved_rates, "skills": resolved_skills}


def _schema_path(error: ValidationError) -> str:
    first = error.errors(include_input=False)[0]
    parts: list[str] = []
    for item in first.get("loc", ()):
        if isinstance(item, int):
            parts[-1:] = [f"{parts[-1]}[{item}]"] if parts else [f"[{item}]"]
        else:
            parts.append(str(item))
    return ".".join(parts) or "submission"


def _code(error: ModelOutputError) -> str:
    return _MESSAGE_CODES.get(str(error), "rejected")


@dataclass(frozen=True)
class SubmissionContext:
    fields: dict[str, str]
    source_hash: str
    request_compensation: bool
    request_skills: bool
    deterministic_skills: tuple[str, ...]


V9_GENERIC_HINT = (
    "Rejected: every evidence text must be copied exactly from the named "
    "section, categories must follow the request flags, and the pay and "
    "skill rules apply. Fix the evidence or return empty lists."
)


def validate_submission(
    payload: dict[str, Any], context: SubmissionContext, *, exact: bool = False
) -> ValidatedExtraction:
    """Resolve evidence, then run the authoritative validator.

    Raises SubmissionRejected with the first failing path and a fixed code.
    `exact=True` reproduces the v9 baseline (exact text only, no resolution)
    for evaluation comparisons.
    """
    resolved = payload if exact else resolve_payload(payload, context.fields)
    try:
        candidate = ExtractionCandidate.model_validate(resolved)
    except ValidationError as error:
        raise SubmissionRejected("schema", _schema_path(error)) from None
    try:
        return validate_extraction(
            candidate,
            context.fields,
            source_hash=context.source_hash,
            request_compensation=context.request_compensation,
            request_skills=context.request_skills,
            deterministic_skills=context.deterministic_skills,
        )
    except ModelOutputError as error:
        raise _diagnose(candidate, context, error) from None
    except ValueError:
        raise SubmissionRejected("rejected") from None


def _diagnose(
    candidate: ExtractionCandidate, context: SubmissionContext, error: ModelOutputError
) -> SubmissionRejected:
    code = _code(error)
    if code == "unrequested_category":
        path = (
            "base_rates"
            if candidate.base_rates and not context.request_compensation
            else "skills"
        )
        return SubmissionRejected(code, path)
    for number, rate in enumerate(candidate.base_rates):
        try:
            _validate_rate(rate, context.fields, context.source_hash)
        except ModelOutputError as rate_error:
            rate_code = _code(rate_error)
            field = (
                rate.eligibility_evidence.field
                if rate_code == "rate_eligibility_not_exact"
                and rate.eligibility_evidence is not None
                else rate.evidence.field
            )
            return SubmissionRejected(rate_code, f"base_rates[{number}]", field)
        except ValueError:
            return SubmissionRejected("rejected", f"base_rates[{number}]")
    for number, skill in enumerate(candidate.skills):
        try:
            _validate_skill(skill, context.fields, context.source_hash)
        except ModelOutputError as skill_error:
            return SubmissionRejected(
                _code(skill_error), f"skills[{number}]", skill.evidence.field
            )
        except ValueError:
            return SubmissionRejected("rejected", f"skills[{number}]")
    return SubmissionRejected(code)


_CODE_PATTERN = re.compile(r"^[a-z_]{1,40}$")


def reason_code(error: Exception) -> str:
    code = error.code if isinstance(error, SubmissionRejected) else "rejected"
    return code if _CODE_PATTERN.fullmatch(code) else "rejected"
