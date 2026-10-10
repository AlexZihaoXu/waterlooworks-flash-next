"""Per-posting detail outcomes with identity-only control sentinels.

No posting records, markup or provider responses are fabricated: storage calls are
replaced by recorders and each posting is an object carrying only a control ID.
"""

import sqlite3
from types import SimpleNamespace
from typing import cast

import pytest

from waterlooworks_flash_next import detail_fetch
from waterlooworks_flash_next.content import StoredPostingContent
from waterlooworks_flash_next.database import StoredListing
from waterlooworks_flash_next.detail_fetch import (
    DetailCollection,
    DetailFetchError,
    DetailProgress,
    DetailSource,
)
from waterlooworks_flash_next.listings import FailureCategory
from waterlooworks_flash_next.ww_pacing import PacingConfig, RequestGate


def control_gate() -> RequestGate:
    # Real gate; collections here replace _content, so it never sleeps.
    return RequestGate(PacingConfig(), daily_remaining=10_000, sleep=lambda s: None)


class Recorder:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.started: list[str] = []
        self.failed: list[tuple[str, str]] = []
        self.completed: list[str] = []
        self.blocked: list[int] = []
        monkeypatch.setattr(detail_fetch, "start_content_attempt", self.start)
        monkeypatch.setattr(
            detail_fetch,
            "record_blocked_until",
            lambda connection, run, seconds: self.blocked.append(seconds),
        )
        monkeypatch.setattr(detail_fetch, "fail_content_attempt", self.fail)
        monkeypatch.setattr(detail_fetch, "complete_content_attempt", self.complete)

    def start(self, connection: object, run_id: str, posting_id: str) -> str:
        self.started.append(posting_id)
        return posting_id

    def fail(
        self,
        connection: object,
        attempt_id: str,
        category: str,
        *,
        http_status: int | None = None,
    ) -> None:
        self.failed.append((attempt_id, category))

    def complete(self, connection: object, attempt_id: str, content: object) -> int:
        self.completed.append(attempt_id)
        return 1


def _postings(count: int) -> list[StoredListing]:
    return [
        cast(StoredListing, SimpleNamespace(id=f"control-{index}"))
        for index in range(count)
    ]


def _collection(
    postings: list[StoredListing],
    failures: dict[str, DetailFetchError],
    progress: list[DetailProgress] | None = None,
) -> DetailCollection:
    source = DetailSource((), {}, (), lambda: True)
    collection = DetailCollection(
        source,
        cast(sqlite3.Connection, None),
        "control-run",
        postings,
        (progress if progress is not None else []).append,
        gate=control_gate(),
    )

    def content(attempt_id: str, posting: StoredListing) -> StoredPostingContent:
        if posting.id in failures:
            raise failures[posting.id]
        return cast(StoredPostingContent, SimpleNamespace(id=posting.id))

    collection._content = content  # type: ignore[method-assign]
    return collection


@pytest.mark.parametrize(
    "category",
    [
        FailureCategory.SCHEMA,
        FailureCategory.SIZE,
        FailureCategory.ENCODING,
    ],
)
def test_posting_validation_failure_is_an_item_outcome_and_pass_continues(
    monkeypatch: pytest.MonkeyPatch, category: FailureCategory
) -> None:
    recorder = Recorder(monkeypatch)
    postings = _postings(3)
    progress: list[DetailProgress] = []
    collection = _collection(
        postings, {"control-1": DetailFetchError(category)}, progress
    )
    collection.run()
    assert recorder.started == ["control-0", "control-1", "control-2"]
    assert recorder.failed == [("control-1", category.value)]
    assert recorder.completed == ["control-0", "control-2"]
    assert collection.completed == 2
    assert collection.failed == 1
    assert progress[-1].failed == 1
    assert progress[-1].completed == 2


@pytest.mark.parametrize(
    "category",
    [
        FailureCategory.AUTH,
        FailureCategory.RATE_LIMIT,
        FailureCategory.REDIRECT,
        FailureCategory.TLS,
        FailureCategory.STALE,
        FailureCategory.NETWORK,
        FailureCategory.TIMEOUT,
        FailureCategory.HTTP,
        FailureCategory.INTERNAL,
    ],
)
def test_session_level_failures_still_abort_the_pass(
    monkeypatch: pytest.MonkeyPatch, category: FailureCategory
) -> None:
    recorder = Recorder(monkeypatch)
    collection = _collection(_postings(3), {"control-0": DetailFetchError(category)})
    with pytest.raises(DetailFetchError) as stopped:
        collection.run()
    assert stopped.value.category == category
    assert recorder.started == ["control-0"]
    assert recorder.failed == [("control-0", category.value)]
    # Ban-risk signals also block refreshes durably; other stops do not.
    assert bool(recorder.blocked) == (
        category in (FailureCategory.AUTH, FailureCategory.RATE_LIMIT)
    )


def test_unexpected_exceptions_still_abort_as_internal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = Recorder(monkeypatch)
    collection = _collection(_postings(2), {})

    def broken(attempt_id: str, posting: StoredListing) -> StoredPostingContent:
        raise RuntimeError("control")

    collection._content = broken  # type: ignore[method-assign]
    with pytest.raises(DetailFetchError) as stopped:
        collection.run()
    assert stopped.value.category == FailureCategory.INTERNAL
    assert recorder.started == ["control-0"]


def test_bootstrap_validation_failure_aborts_because_no_session_remains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = Recorder(monkeypatch)

    def bootstrap(posting: object, request: object) -> DetailSource:
        raise DetailFetchError(FailureCategory.SCHEMA)

    collection = DetailCollection(
        None,
        cast(sqlite3.Connection, None),
        "control-run",
        _postings(2),
        lambda progress: None,
        bootstrap=bootstrap,
        gate=control_gate(),
    )
    with pytest.raises(DetailFetchError):
        collection.run()
    assert recorder.started == ["control-0"]
    assert recorder.failed == [("control-0", FailureCategory.SCHEMA.value)]


def test_consecutive_item_failures_abort_as_a_broken_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = Recorder(monkeypatch)
    limit = DetailCollection.MAX_CONSECUTIVE_ITEM_FAILURES
    postings = _postings(limit + 5)
    collection = _collection(
        postings,
        {
            posting.id: DetailFetchError(FailureCategory.SCHEMA)
            for posting in postings[1:]
        },
    )
    with pytest.raises(DetailFetchError) as stopped:
        collection.run()
    assert stopped.value.category == FailureCategory.SCHEMA
    assert len(recorder.failed) == limit
    assert len(recorder.started) == limit + 1
