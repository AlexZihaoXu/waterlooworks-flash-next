"""Explicit accepted historical read identities, never arbitrary obsolete output."""

import sqlite3
from typing import cast

from waterlooworks_flash_next.model_validation import (
    INPUT_VERSION,
    VALIDATOR_VERSION,
)
from waterlooworks_flash_next.model_validation import (
    TEMPLATE_VERSION as LEGACY_TEMPLATE_VERSION,
)
from waterlooworks_flash_next.openai_provider import TEMPLATE_VERSION
from waterlooworks_flash_next.openai_usage import MODEL

LEGACY_MODEL_ID = "qwen3.8-27b@q4_k_m"


_TRUSTED = """SELECT a.*,cache.cache_id FROM effective_model_projections e
        JOIN analysis_projections a ON a.projection_id=e.projection_id
        JOIN inference_cache cache ON cache.cache_id=e.cache_id
        WHERE cache.content_hash=a.content_hash
          AND cache.input_version=? AND cache.validator_version=?
          AND cache.max_tokens=4096 AND (
            (cache.model_id=? AND cache.template_version=?) OR
            (cache.model_id=? AND cache.template_version=?))"""


def _trusted_parameters() -> tuple[str, ...]:
    return (
        INPUT_VERSION,
        VALIDATOR_VERSION,
        MODEL,
        TEMPLATE_VERSION,
        LEGACY_MODEL_ID,
        LEGACY_TEMPLATE_VERSION,
    )


def trusted_model_projection(
    connection: sqlite3.Connection, posting_id: str
) -> sqlite3.Row | None:
    return cast(
        sqlite3.Row | None,
        connection.execute(
            _TRUSTED + " AND e.posting_id=?", (*_trusted_parameters(), posting_id)
        ).fetchone(),
    )


def trusted_model_projections(connection: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    """Same acceptance rule as the per-posting reader, in one set-based query.

    The model pointer is keyed by posting, so there is at most one row each.
    """
    return {
        row["posting_id"]: row
        for row in connection.execute(_TRUSTED, _trusted_parameters())
    }
