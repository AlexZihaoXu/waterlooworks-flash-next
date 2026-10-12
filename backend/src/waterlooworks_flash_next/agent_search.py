"""Server-side search, filters, sorting and paging for the agent API.

Mirrors the web app's client-side semantics (``frontend/src/lib/workspace.ts``
and ``sorting.ts``) over the cached member workspace snapshot:

* ``q``: every whitespace-separated word must appear (case-folded substring)
  in some text field: posting ID, title, organization, location/city, or the
  accepted inferred country name/code. Words may match different fields.
* Scalar filters (organization, city, country, arrangement) are
  case-insensitive exact matches, OR within a filter and AND across filters;
  disciplines match exact saved source values; ``state``/``viewed`` use the
  caller's private marks.
* Sorts use the web sort keys. Unknown values always sort last; ties fall back
  to numeric posting ID order. Compensation sorts rank by the stored summary
  without currency conversion (the web app additionally estimates hourly
  equivalents and approximate FX for display).
"""

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from functools import cmp_to_key
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from waterlooworks_flash_next.api_models import (
    AgentPostingSummary,
    CompensationSort,
    PostingResponse,
)
from waterlooworks_flash_next.posting_marks import PostingMark

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20
MAX_VALUES = 50
MAX_TEXT = 200
TORONTO = ZoneInfo("America/Toronto")
_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
_DEADLINE = re.compile(
    r"\A([A-Z][a-z]{2}) (\d{1,2}), (\d{4}) (\d{1,2}):(\d{2}) (AM|PM)\Z", re.ASCII
)
_COUNTRY_CODE = re.compile(r"\A[A-Z]{2}\Z", re.ASCII)

SortKey = Literal[
    "id",
    "newest",
    "remaining",
    "applications-desc",
    "openings-desc",
    "organization",
    "compensation-average",
    "compensation-minimum",
    "compensation-maximum",
]
StateFilter = Literal["interested", "applied", "not_interested", "none"]
Arrangement = Literal["Remote", "Hybrid", "In-person"]
Text = Annotated[str, Field(max_length=MAX_TEXT)]
Values = Annotated[list[Text], Field(max_length=MAX_VALUES)]


class PostingQuery(BaseModel):
    """Query parameters of ``GET /api/agent/v1/postings``; unknown ones are refused."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    q: str = Field(
        default="",
        max_length=MAX_TEXT,
        description=(
            "Keyword search: every word must match some text field "
            "(ID, title, organization, location, inferred country)."
        ),
    )
    organization: Values = Field(
        default_factory=list, description="Exact organization names (any of)."
    )
    city: Values = Field(
        default_factory=list, description="Exact stored location values (any of)."
    )
    country: Values = Field(
        default_factory=list,
        description="Accepted inferred country names or ISO codes (any of).",
    )
    discipline: Values = Field(
        default_factory=list,
        description="Exact targeted discipline source values (any of).",
    )
    arrangement: Annotated[list[Arrangement], Field(max_length=3)] = Field(
        default_factory=list, description="Remote, Hybrid or In-person (any of)."
    )
    state: Annotated[list[StateFilter], Field(max_length=4)] = Field(
        default_factory=list,
        description="Caller's state: interested, applied, not_interested or none.",
    )
    exclude_not_interested: bool = Field(
        default=False, description="Drop postings the caller marked not_interested."
    )
    viewed: bool | None = Field(
        default=None, description="Only viewed (true) or unviewed (false) postings."
    )
    sort: SortKey = Field(default="id", description="Web app sort key.")
    page: int = Field(default=1, ge=1, le=100_000)
    page_size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)


def search_words(query: str) -> list[str]:
    return query.casefold().split()


def matches_search(values: Iterable[object], query: str) -> bool:
    """Every query word must appear in some string value; words may span fields."""
    words = search_words(query)
    if not words:
        return True
    fields = [value.casefold() for value in values if isinstance(value, str)]
    return all(any(word in field for field in fields) for word in words)


def count_value(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        return int(value.strip())
    return None


def parse_deadline(value: object) -> datetime | None:
    """Verified listing display format (Eastern Time); DST gaps/repeats stay unknown."""
    if not isinstance(value, str):
        return None
    match = _DEADLINE.match(value.strip())
    if match is None or match[1] not in _MONTHS:
        return None
    hour = int(match[4])
    if not 1 <= hour <= 12:
        return None
    hours = hour % 12 + (12 if match[6] == "PM" else 0)
    try:
        wall = datetime(
            int(match[3]),
            _MONTHS.index(match[1]) + 1,
            int(match[2]),
            hours,
            int(match[5]),
            tzinfo=TORONTO,
        )
    except ValueError:
        return None
    if (
        wall.astimezone(UTC).astimezone(TORONTO).replace(tzinfo=None)
        != wall.replace(tzinfo=None)
        or wall.replace(fold=1).utcoffset() != wall.utcoffset()
    ):
        return None
    return wall.astimezone(UTC)


def inferred_country(posting: PostingResponse) -> tuple[str | None, str | None]:
    inference = posting.country_inference
    code = inference.country_code
    if (
        inference.state == "accepted"
        and isinstance(code, str)
        and _COUNTRY_CODE.match(code)
    ):
        return code, inference.country_name
    return None, None


def _clean(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _selected(value: str | None, selected: Sequence[str]) -> bool:
    if not selected:
        return True
    if value is None:
        return False
    folded = value.casefold()
    return any(item.strip().casefold() == folded for item in selected)


def _matches(
    posting: PostingResponse, mark: PostingMark | None, query: PostingQuery
) -> bool:
    listing = posting.listing
    code, name = inferred_country(posting)
    state = mark.state if mark else None
    viewed = mark.viewed_at if mark else None
    if query.exclude_not_interested and state == "not_interested":
        return False
    if query.state and (state or "none") not in query.state:
        return False
    if query.viewed is not None and (viewed is not None) != query.viewed:
        return False
    if query.arrangement and posting.work_arrangement not in query.arrangement:
        return False
    if not _selected(_clean(listing.organization), query.organization):
        return False
    if not _selected(_clean(posting.location), query.city):
        return False
    if query.country and not (
        _selected(name, query.country) or _selected(code, query.country)
    ):
        return False
    if query.discipline and not set(posting.targeted_disciplines) & set(
        query.discipline
    ):
        return False
    return matches_search(
        [
            posting.posting_id,
            listing.title,
            listing.organization,
            posting.location,
            listing.city,
            name,
            code,
        ],
        query.q,
    )


def _id_key(posting: PostingResponse) -> tuple[int, str]:
    return (int(posting.posting_id), posting.posting_id)


def _cmp(left: str | Decimal, right: str | Decimal) -> int:
    return int(left > right) - int(left < right)  # type: ignore[operator]


def _is_zero(value: str) -> bool:
    return re.fullmatch(r"0+(?:\.0+)?", value) is not None


def _pay_value(pay: CompensationSort | None, metric: str) -> str | None:
    if pay is None:
        return "0"
    if pay.needs_review or pay.period == "day":
        return None
    if pay.minimum is None and pay.maximum is None and not pay.source_rates:
        return "0"
    value: str | None = getattr(pay, metric)
    return value if pay.period else None


def compare_compensation(
    left: CompensationSort | None, right: CompensationSort | None, metric: str
) -> int:
    """Highest first within currency/period cohorts; zero and unknown last."""
    a, b = _pay_value(left, metric), _pay_value(right, metric)
    if a is None:
        return 0 if b is None else 1
    if b is None:
        return -1
    zero_a, zero_b = _is_zero(a), _is_zero(b)
    if zero_a or zero_b:
        return (0 if zero_b else 1) if zero_a else -1
    assert left is not None and right is not None
    cohort = _cmp(left.currency or "CAD", right.currency or "CAD") or _cmp(
        left.period or "", right.period or ""
    )
    if cohort:
        return cohort
    try:
        return -_cmp(Decimal(a), Decimal(b))
    except InvalidOperation:
        return 0


def sort_postings(
    postings: Sequence[PostingResponse], sort: SortKey, now: datetime | None = None
) -> list[PostingResponse]:
    now = now or datetime.now(UTC)
    ordered = sorted(postings, key=_id_key)
    if sort == "id":
        return ordered
    if sort.startswith("compensation-"):
        metric = sort.removeprefix("compensation-")

        def by_pay(a: PostingResponse, b: PostingResponse) -> int:
            return compare_compensation(
                a.compensation_sort, b.compensation_sort, metric
            )

        return sorted(ordered, key=cmp_to_key(by_pay))
    if sort == "newest":
        return sorted(ordered, key=lambda p: p.first_seen_at, reverse=True)
    if sort == "organization":
        return sorted(
            ordered,
            key=lambda p: (
                _clean(p.listing.organization) is None,
                (_clean(p.listing.organization) or "").casefold(),
            ),
        )
    if sort == "remaining":
        deadlines = {p.posting_id: parse_deadline(p.listing.deadline) for p in ordered}

        def remaining(posting: PostingResponse) -> tuple[int, float]:
            deadline = deadlines[posting.posting_id]
            if deadline is None:
                return (2, 0.0)
            if deadline > now:
                return (0, deadline.timestamp())
            return (1, -deadline.timestamp())

        return sorted(ordered, key=remaining)
    field = "application_count" if sort == "applications-desc" else "openings"

    def count(posting: PostingResponse) -> tuple[int, int]:
        value = count_value(getattr(posting.listing, field))
        return (1, 0) if value is None else (0, -value)

    return sorted(ordered, key=count)


def select_postings(
    postings: Sequence[PostingResponse],
    marks: Mapping[str, PostingMark],
    query: PostingQuery,
    now: datetime | None = None,
) -> list[PostingResponse]:
    matched = [p for p in postings if _matches(p, marks.get(p.posting_id), query)]
    return sort_postings(matched, query.sort, now)


@dataclass(frozen=True)
class Page[T]:
    items: list[T]
    page: int
    page_size: int
    total: int

    @property
    def total_pages(self) -> int:
        return math.ceil(self.total / self.page_size)


def paginate[T](items: Sequence[T], page: int, page_size: int) -> Page[T]:
    start = (page - 1) * page_size
    return Page(list(items[start : start + page_size]), page, page_size, len(items))


def summarize(
    posting: PostingResponse, mark: PostingMark | None
) -> AgentPostingSummary:
    listing = posting.listing
    code, name = inferred_country(posting)
    return AgentPostingSummary(
        posting_id=posting.posting_id,
        title=listing.title,
        organization=listing.organization,
        division=listing.division,
        location=posting.location,
        openings=listing.openings,
        level=listing.level,
        application_count=listing.application_count,
        deadline=listing.deadline,
        work_arrangement=posting.work_arrangement,
        country_code=code,
        country_name=name,
        targeted_disciplines=posting.targeted_disciplines,
        compensation=posting.compensation_sort,
        first_seen_at=posting.first_seen_at,
        last_seen_at=posting.last_seen_at,
        state=mark.state if mark else None,
        viewed_at=mark.viewed_at if mark else None,
    )
