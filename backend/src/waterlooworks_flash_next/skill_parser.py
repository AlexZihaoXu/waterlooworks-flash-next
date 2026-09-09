"""Small reviewed explicit-tech lexicon, not an exhaustive semantic extractor."""

import re
from collections.abc import Mapping
from typing import Literal

from pydantic import Field, JsonValue

from waterlooworks_flash_next.parser_models import (
    MAX_FIELD_LENGTH,
    PARSER_VERSION,
    ParserModel,
    ParseState,
    Requirement,
    SourceSpan,
)

SKILL_FIELDS = ("Required Skills:", "Job Responsibilities:", "Job Summary:")
# Aliases are explicit spellings only. No implied skills from roles or disciplines.
_VOCABULARY = {
    "C++": r"C\+\+",
    "C#": r"C\#",
    "Java": r"Java",
    "JavaScript": r"JavaScript",
    "TypeScript": r"TypeScript",
    "Python": r"Python",
    "SQL": r"SQL",
    "Kubernetes": r"Kubernetes",
    "Docker": r"Docker",
    "Git": r"Git",
    "Linux": r"Linux",
    "Node.js": r"Node\.js",
    ".NET": r"\.NET",
    "HTML": r"HTML",
    "CSS": r"CSS",
    "CI/CD": r"CI[/\-]CD",
    "Object-oriented design": r"object[- ]oriented design",
}
_PATTERNS = {
    name: re.compile(r"(?<![\w+#])(?:" + pattern + r")(?![\w+#])", re.I)
    for name, pattern in _VOCABULARY.items()
}
_NEGATION = re.compile(r"\b(?:no|not|without|never|optional)\b", re.I)
_REQUIRED = re.compile(r"\b(?:required|require|requires|must|mandatory)\b", re.I)
_PREFERRED = re.compile(
    r"\b(?:preferred|prefer|bonus|asset|nice[- ]to[- ]have)\b", re.I
)
_CLAUSE = re.compile(r"[;\n!?]|\.(?:\s|$)")


class SkillMention(ParserModel):
    skill: str
    requirement: Requirement
    requirement_evidence: SourceSpan | None = None
    evidence: SourceSpan


class ExcludedSkill(ParserModel):
    skill: str
    reason: Literal["negated_or_optional_context"]
    evidence: SourceSpan


class SkillsResult(ParserModel):
    parser_version: str = PARSER_VERSION
    state: ParseState
    coverage: Literal["bounded_explicit_vocabulary"] = "bounded_explicit_vocabulary"
    source_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_fields: dict[str, str] = Field(repr=False)
    matches: tuple[SkillMention, ...]
    excluded: tuple[ExcludedSkill, ...]
    unscanned_fields: tuple[str, ...]


def parse_skills(
    fields: Mapping[str, JsonValue], *, source_hash: str | None = None
) -> SkillsResult:
    matches: list[SkillMention] = []
    excluded: list[ExcludedSkill] = []
    source_fields: dict[str, str] = {}
    unscanned: list[str] = []
    for field in SKILL_FIELDS:
        raw = fields.get(field)
        if not isinstance(raw, str):
            continue
        source_fields[field] = raw
        if len(raw) > MAX_FIELD_LENGTH:
            unscanned.append(field)
            continue
        # Split once, rather than repeatedly scanning a 200k-character field per token.
        boundaries = [0, *(match.end() for match in _CLAUSE.finditer(raw)), len(raw)]
        for start, end in zip(boundaries, boundaries[1:], strict=False):
            clause = raw[start:end]
            negated = bool(_NEGATION.search(clause))
            required, preferred = (
                bool(_REQUIRED.search(clause)),
                bool(_PREFERRED.search(clause)),
            )
            requirement: Requirement = (
                "required"
                if required and not preferred
                else "preferred"
                if preferred and not required
                else "unspecified"
            )
            mentions = [
                (start + hit.start(), start + hit.end(), skill)
                for skill, pattern in _PATTERNS.items()
                for hit in pattern.finditer(clause)
            ]
            for begin, finish, skill in sorted(mentions):
                evidence = SourceSpan(
                    field=field,
                    start=begin,
                    end=finish,
                    text=raw[begin:finish],
                    source_hash=source_hash,
                )
                if negated:
                    excluded.append(
                        ExcludedSkill(
                            skill=skill,
                            reason="negated_or_optional_context",
                            evidence=evidence,
                        )
                    )
                else:
                    matches.append(
                        SkillMention(
                            skill=skill,
                            requirement=requirement,
                            evidence=evidence,
                            requirement_evidence=(
                                SourceSpan(
                                    field=field,
                                    start=start + qualifier.start(),
                                    end=start + qualifier.end(),
                                    text=qualifier[0],
                                    source_hash=source_hash,
                                )
                                if (
                                    qualifier := (
                                        _REQUIRED.search(clause)
                                        if requirement == "required"
                                        else _PREFERRED.search(clause)
                                        if requirement == "preferred"
                                        else None
                                    )
                                )
                                else None
                            ),
                        )
                    )
    return SkillsResult(
        state="recognized" if matches else "unknown",
        source_hash=source_hash,
        source_fields=source_fields,
        matches=tuple(matches),
        excluded=tuple(excluded),
        unscanned_fields=tuple(unscanned),
    )
