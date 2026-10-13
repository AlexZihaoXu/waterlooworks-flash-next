"""When a saved posting is ready to appear in a Discord new-job alert.

A refresh caps detail requests and AI extraction per run, so a posting can be
saved from the listing long before its details and pay are known. Alerts wait
until the pipeline has finished with the posting:

1. Its detail content is saved by the current extractor
   (``posting_contents.extractor_version == EXTRACTOR_VERSION``).
2. When AI extraction is activated, an extraction of exactly that saved
   content (``inference_job_items.selected_content_hash`` equal to the current
   ``posting_contents.content_hash``) reached a terminal outcome:

   - ``accepted``: facts saved;
   - ``rejected`` or ``failed``: facts unavailable, they stay unknown;
   - ``not_needed`` without an error category: the deterministic parser already
     knew the pay and skills, so no model request was needed;

   or a current model projection exists for that content. ``not_needed`` with
   ``stopped`` (deferred by the per-run posting cap), ``interrupted``,
   ``pending`` and ``running`` are not terminal.

Without an active AI configuration, saved detail content alone is ready.
"""

import sqlite3
from pathlib import Path

from waterlooworks_flash_next.content_storage import EXTRACTOR_VERSION

_READY = """
    SELECT c.posting_id FROM posting_contents c
    WHERE c.extractor_version=:extractor
      AND (NOT :ai_active
        OR EXISTS (
          SELECT 1 FROM inference_job_items i
          WHERE i.posting_id=c.posting_id
            AND i.selected_content_hash=c.content_hash
            AND (i.status IN ('accepted','rejected','failed')
              OR (i.status='not_needed' AND i.error_category IS NULL)))
        OR EXISTS (
          SELECT 1 FROM effective_model_projections e
          WHERE e.posting_id=c.posting_id))
"""


def ready_posting_ids(connection: sqlite3.Connection, *, ai_active: bool) -> set[str]:
    """IDs of every saved posting the pipeline has finished with (see module)."""
    return {
        str(row[0])
        for row in connection.execute(
            _READY, {"extractor": EXTRACTOR_VERSION, "ai_active": int(ai_active)}
        )
    }


def ai_extraction_active(path: Path) -> bool:
    """Whether an AI configuration is explicitly activated (never the draft)."""
    from waterlooworks_flash_next.inference import InferenceError, active_config

    if not path.exists():
        return False
    try:
        active_config(path)
    except InferenceError:
        return False
    return True
