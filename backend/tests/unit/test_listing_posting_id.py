"""Listing rows accept only ASCII decimal posting IDs."""

import pytest

from waterlooworks_flash_next.listings import ListingFetchError, ListingRow


@pytest.mark.parametrize("value", ["٣٤", "²", "１２", "", " ", "12a"])
def test_non_ascii_or_malformed_ids_are_refused(value: str) -> None:
    row = ListingRow.model_validate({"data": [{"key": "Id", "value": value}]})
    with pytest.raises(ListingFetchError):
        row.posting_id()


def test_ascii_ids_are_kept() -> None:
    row = ListingRow.model_validate({"data": [{"key": "Id", "value": " 492630 "}]})
    assert row.posting_id() == "492630"
    assert (
        ListingRow.model_validate({"data": [{"key": "Id", "value": 7}]}).posting_id()
        == "7"
    )
