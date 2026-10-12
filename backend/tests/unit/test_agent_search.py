"""Pure search/filter/sort/pagination helpers for the agent API.

Inputs are plain strings, numbers and ID-only structural posting records; no
posting content or platform responses are invented.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from waterlooworks_flash_next.agent_search import (
    MAX_PAGE_SIZE,
    PostingQuery,
    count_value,
    matches_search,
    paginate,
    parse_deadline,
    search_words,
    select_postings,
)
from waterlooworks_flash_next.api_models import PostingResponse
from waterlooworks_flash_next.database import StoredListing
from waterlooworks_flash_next.posting_marks import PostingMark


def _posting(
    posting_id: str, seen: str = "2026-10-01T00:00:00+00:00"
) -> PostingResponse:
    return PostingResponse(
        posting_id=posting_id,
        listing=StoredListing(id=posting_id),
        first_seen_at=seen,
        last_seen_at=seen,
        location=None,
    )


def _mark(
    posting_id: str, state: str | None = None, viewed: str | None = None
) -> PostingMark:
    return PostingMark(
        posting_id=posting_id,
        state=state,  # type: ignore[arg-type]
        viewed_at=viewed,
        updated_at="2026-10-02T00:00:00+00:00",
    )


def test_search_words_and_matching_mirror_the_web_search() -> None:
    assert search_words("  Alpha   beta ") == ["alpha", "beta"]
    assert search_words("") == []
    assert matches_search(["Alpha Beta", None, 3], "")
    assert matches_search(["Alpha Beta", None], "alpha")
    assert matches_search(["Alpha", "Beta"], "alpha beta")  # words span fields
    assert matches_search(["Alpha", None], "alpha gamma") is False
    assert matches_search([], "x") is False
    assert matches_search(["ÉCOLE"], "école")


def test_count_and_deadline_parsing_follow_the_display_formats() -> None:
    assert count_value(3) == 3
    assert count_value(" 12 ") == 12
    assert count_value("12a") is None
    assert count_value(-1) is None
    assert count_value(None) is None
    assert count_value(True) is None
    assert parse_deadline("Oct 15, 2026 11:59 PM") == datetime(
        2026, 10, 16, 3, 59, tzinfo=UTC
    )
    assert parse_deadline("Jan 5, 2026 12:00 AM") == datetime(
        2026, 1, 5, 5, 0, tzinfo=UTC
    )
    for value in ("2026-10-15", "Oct 15, 2026", "Oct 15, 2026 13:59 PM", None, 5):
        assert parse_deadline(value) is None


def test_query_bounds_and_unknown_parameters_are_rejected() -> None:
    assert PostingQuery().page_size == 20
    assert PostingQuery(page=3, page_size=MAX_PAGE_SIZE).page == 3
    for bad in (
        {"page": 0},
        {"page_size": 0},
        {"page_size": MAX_PAGE_SIZE + 1},
        {"sort": "random"},
        {"state": ["maybe"]},
        {"q": "x" * 201},
        {"organization": ["x"] * 51},
        {"organization": ["x" * 201]},
        {"arrangement": ["Office"]},
        {"unknown": "1"},
        {"viewed": "maybe"},
    ):
        with pytest.raises(ValidationError):
            PostingQuery.model_validate(bad)


def test_state_viewed_filters_sorting_and_pagination() -> None:
    postings = [
        _posting("3", "2026-10-03T00:00:00+00:00"),
        _posting("10", "2026-10-01T00:00:00+00:00"),
        _posting("2", "2026-10-02T00:00:00+00:00"),
    ]
    marks = {
        "3": _mark("3", "applied"),
        "10": _mark("10", "not_interested", "2026-10-02T00:00:00+00:00"),
    }

    def ids(**query: object) -> list[str]:
        selected = select_postings(postings, marks, PostingQuery.model_validate(query))
        return [posting.posting_id for posting in selected]

    assert ids() == ["2", "3", "10"]  # numeric-aware ID order
    assert ids(sort="newest") == ["3", "2", "10"]
    assert ids(sort="remaining") == ["2", "3", "10"]  # all unknown: ID order
    assert ids(sort="organization") == ["2", "3", "10"]
    assert ids(sort="compensation-average") == ["2", "3", "10"]
    assert ids(state=["applied"]) == ["3"]
    assert ids(state=["none"]) == ["2"]
    assert ids(state=["none", "not_interested"]) == ["2", "10"]
    assert ids(exclude_not_interested=True) == ["2", "3"]
    assert ids(state=["not_interested"], exclude_not_interested=True) == []
    assert ids(viewed=True) == ["10"]
    assert ids(viewed=False) == ["2", "3"]
    assert ids(q="1") == ["10"]
    assert ids(q="nothing") == []
    assert ids(organization=["anything"]) == []
    assert ids(city=["anything"]) == []
    assert ids(country=["anything"]) == []
    assert ids(discipline=["anything"]) == []
    assert ids(arrangement=["Remote"]) == []
    page = paginate(postings, page=2, page_size=2)
    assert page.total == 3 and page.total_pages == 2
    assert [posting.posting_id for posting in page.items] == ["2"]
    beyond = paginate(postings, page=5, page_size=2)
    assert beyond.items == [] and beyond.total == 3
    nothing: list[PostingResponse] = []
    empty = paginate(nothing, page=1, page_size=20)
    assert empty.total == 0 and empty.total_pages == 0
