"""Explicit accepted historical read identities, never arbitrary obsolete output."""

import sqlite3
from typing import cast

from waterlooworks_flash_next.inference_prompt import (
    LEGACY_RESPONSES_MODEL,
    LEGACY_RESPONSES_TEMPLATE,
    PI_TRUSTED_TEMPLATES,
)
from waterlooworks_flash_next.model_validation import (
    INPUT_VERSION,
    VALIDATOR_VERSION,
)
from waterlooworks_flash_next.model_validation import (
    TEMPLATE_VERSION as LEGACY_TEMPLATE_VERSION,
)

LEGACY_MODEL_ID = "qwen3.8-27b@q4_k_m"


# Pi rows carry their configuration hash in the template version
# ("<pi template>+<hash>" for every trusted Pi template) and their access
# mode/model in "pi:<mode>:<model>";
# their max output tokens are part of that configuration, so any bound is valid.
_TRUSTED = """SELECT a.*,cache.cache_id FROM effective_model_projections e
        JOIN analysis_projections a ON a.projection_id=e.projection_id
        JOIN inference_cache cache ON cache.cache_id=e.cache_id
        WHERE cache.content_hash=a.content_hash
          AND cache.input_version=? AND cache.validator_version=? AND (
            (cache.max_tokens=4096 AND cache.model_id=? AND cache.template_version=?)
            OR (cache.max_tokens=4096 AND cache.model_id=?
                AND cache.template_version=?)
            OR (substr(cache.model_id,1,3)='pi:' AND ("""
_TRUSTED += " OR ".join(
    "substr(cache.template_version,1,?)=?" for _ in PI_TRUSTED_TEMPLATES
)
_TRUSTED += ")))"


def _trusted_parameters() -> tuple[str | int, ...]:
    prefixes: list[str | int] = []
    for template in PI_TRUSTED_TEMPLATES:
        prefixes += [len(template) + 1, template + "+"]
    return (
        INPUT_VERSION,
        VALIDATOR_VERSION,
        LEGACY_RESPONSES_MODEL,
        LEGACY_RESPONSES_TEMPLATE,
        LEGACY_MODEL_ID,
        LEGACY_TEMPLATE_VERSION,
        *prefixes,
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
