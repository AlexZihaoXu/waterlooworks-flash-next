"""Empty database and primitive evidence/precedence contracts, no postings."""

from pathlib import Path

from waterlooworks_flash_next.api_models import PostingAnalysisResponse
from waterlooworks_flash_next.database import open_database
from waterlooworks_flash_next.source_enrichment import (
    extraction_state,
    read_source_enrichment,
    valid_span,
)


def test_source_enrichment_empty_read_does_not_create_work(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "empty.sqlite3")
    before = connection.total_changes
    assert read_source_enrichment(connection) == {}
    assert connection.total_changes == before
    assert (
        connection.execute("SELECT count(*) FROM source_extraction_runs").fetchone()[0]
        == 0
    )
    connection.close()


def test_terminal_status_distinguishes_empty_partial_pending_and_stale() -> None:
    assert extraction_state([], usable=0) == "not_requested"
    assert extraction_state(["accepted"], usable=0) == "empty"
    assert extraction_state(["accepted"], usable=1) == "accepted"
    assert extraction_state(["accepted", "rejected"], usable=1) == "partial"
    assert extraction_state(["partial"], usable=0) == "partial"
    assert extraction_state(["rejected"], usable=0) == "rejected"
    assert extraction_state(["failed"], usable=0) == "failed"
    assert extraction_state(["accepted", "pending"], usable=1) == "pending"
    assert extraction_state(["accepted", "stale"], usable=1) == "stale"


def test_evidence_offsets_are_exact_unicode_code_points() -> None:
    assert valid_span("a😀b", 1, 2) == "😀"
    assert valid_span("a😀b", 2, 4) is None
    assert valid_span("abc", -1, 2) is None
    assert valid_span("abc", 1, 1) is None


def test_analysis_separates_keywords_from_required_skills_and_legacy_status() -> None:
    properties = PostingAnalysisResponse.model_json_schema()["properties"]
    assert {
        "keywords",
        "salary_extraction",
        "keyword_extraction",
        "compensation_needs_review",
    } <= properties.keys()
    assert "skills" in properties
    assert "model_status" in properties


def test_rate_clause_reconciliation_preserves_metadata_not_conflicts() -> None:
    from waterlooworks_flash_next.api_models import CompensationRate
    from waterlooworks_flash_next.source_enrichment import (
        SourcePay,
        legacy_amount_span,
        merge_compensation,
    )

    # Primitive numeric rates with empty source fields, not posting fixtures.
    rate = CompensationRate(
        bound_type="fixed",
        minimum="20",
        maximum="20",
        minimum_inclusive=True,
        maximum_inclusive=True,
        currency=None,
        period=None,
        hours_per_week=None,
        payout_frequency=None,
        eligibility=None,
        source_field="",
        evidence_text="",
    )
    richer = rate.model_copy(
        update={"hours_per_week": "32", "payout_frequency": "weekly"}
    )
    extracted = rate.model_copy(update={"currency": "USD", "period": "hour"})
    merged, review = merge_compensation(
        [SourcePay(richer, 0, 2)], [SourcePay(extracted, 0, 2)]
    )
    assert not review and len(merged) == 1
    assert merged[0].hours_per_week == "32"
    assert merged[0].payout_frequency == "weekly"
    assert merged[0].currency == "USD"
    assert richer.currency is None  # No mutation of shared evidence.
    incompatible = extracted.model_copy(update={"currency": "CAD"})
    merged, review = merge_compensation(
        [SourcePay(extracted, 0, 2)], [SourcePay(incompatible, 0, 2)]
    )
    assert review and merged == []
    # A shared broad quote is not itself a duplicate: distinct amount spans survive.
    merged, review = merge_compensation(
        [SourcePay(extracted, 0, 2)], [SourcePay(incompatible, 5, 7)]
    )
    assert not review and len(merged) == 2
    merged, review = merge_compensation(
        [], [SourcePay(rate, 0, 2), SourcePay(rate, 0, 2)]
    )
    assert not review and merged == [rate]
    assert merged[0].period is None and merged[0].currency is None
    assert merge_compensation([SourcePay(richer, 0, 2)], []) == ([richer], False)
    repeated = rate.model_copy(update={"evidence_text": "20 20"})
    ambiguous = SourcePay(repeated, *legacy_amount_span(repeated, 0, 5))
    merged, review = merge_compensation(
        [ambiguous], [SourcePay(extracted, 0, 2), SourcePay(extracted, 3, 5)]
    )
    assert review and merged == []
    assert merge_compensation([ambiguous], []) == ([repeated], False)
    unique = rate.model_copy(update={"evidence_text": "20"})
    assert legacy_amount_span(unique, 4, 6) == (4, 6, True)

    from waterlooworks_flash_next.source_enrichment import refine_pay_evidence

    quoted = rate.model_copy(update={"evidence_text": "20 / month (USD)"})
    recovered = refine_pay_evidence(quoted, 0, 10)
    assert recovered is not None and recovered.currency == "USD"
    assert quoted.currency is None
    currency_ambiguous = quoted.model_copy(
        update={"evidence_text": "20 / month CAD or USD"}
    )
    unchanged = refine_pay_evidence(currency_ambiguous, 0, 20)
    assert unchanged is not None and unchanged.currency is None
    other = quoted.model_copy(update={"evidence_text": "20 plus 7 CAD bonus"})
    unrelated = refine_pay_evidence(other, 0, 2)
    assert unrelated is not None and unrelated.currency is None
    hours = quoted.model_copy(update={"evidence_text": "20/hr work week"})
    assert refine_pay_evidence(hours, 0, 5) is None
    actual = quoted.model_copy(
        update={"evidence_text": "$20 USD/hr with a 37/hr work week"}
    )
    refined = refine_pay_evidence(actual, 0, 10)
    assert refined is not None and refined.currency == "USD"
    assert refined.hours_per_week == "37"
    wage = quoted.model_copy(update={"evidence_text": "$20/hr work week"})
    assert refine_pay_evidence(wage, 0, 6) is not None
    comma = quoted.model_copy(update={"evidence_text": "$20/hr, 37,5 hours per week"})
    comma_rate = refine_pay_evidence(comma, 0, 6)
    assert comma_rate is not None and comma_rate.hours_per_week == "37.5"
    grouped = quoted.model_copy(
        update={"evidence_text": "$20/hr, 1,000 hours per week"}
    )
    grouped_rate = refine_pay_evidence(grouped, 0, 6)
    assert grouped_rate is not None and grouped_rate.hours_per_week is None


def test_analysis_endpoint_owns_read_transaction(tmp_path: Path) -> None:
    import sqlite3

    import pytest
    from auth_support import session_client

    from waterlooworks_flash_next import api
    from waterlooworks_flash_next.analysis_read import read_analysis

    observed: list[bool] = []
    original = read_analysis

    def observe(
        connection: sqlite3.Connection, posting_id: str
    ) -> PostingAnalysisResponse:
        observed.append(connection.in_transaction)
        return original(connection, posting_id)

    # Observe the real reader; do not manufacture a response or posting.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(api, "read_analysis", observe)
        with session_client(
            tmp_path / "empty.sqlite3", app=api.create_app(tmp_path / "empty.sqlite3")
        ) as client:
            assert client.get("/api/postings/1/analysis").status_code == 404
    assert observed == [True]
