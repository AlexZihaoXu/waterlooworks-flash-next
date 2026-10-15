"""Saved analysis follows the description, never the volatile listing revision.

Listings change on nearly every pass (application counts). Pay and skills come
from the saved description, so a listing change must not hide them.
"""

import re

from waterlooworks_flash_next import analysis_read


def _conditions() -> list[str]:
    source = analysis_read.__file__
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    return re.findall(
        r"WHERE a\.(?:posting_id<>\? AND a\.)?producer_kind=.*?\"\"\"", text, re.S
    )


def test_current_projection_reads_require_the_current_description_only() -> None:
    blocks = _conditions()
    assert len(blocks) == 2  # workspace/detail reader and similar-jobs reader
    for block in blocks:
        assert "a.content_hash=c.content_hash" in block
        assert "content_revision_id" in block
        assert "listing_hash" not in block
        assert "listing_revision_id" not in block
