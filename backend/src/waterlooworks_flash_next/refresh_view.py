"""Role-scoped refresh state: members get timing and progress only.

The refresh log, request accounting, failure categories and AI provider
details are admin diagnostics; every member-facing route and stream sends
this reduced view instead.
"""

from waterlooworks_flash_next.api_models import RefreshSnapshot

MEMBER_FIELDS = (
    "run_id",
    "status",
    "stage",
    "pages_completed",
    "total_pages",
    "details_completed",
    "details_total",
    "analysis_total",
    "analysis_completed",
    "eta_seconds",
    "next_scheduled_at",
    "scheduled_failure",
)


def member_refresh_view(snapshot: RefreshSnapshot) -> RefreshSnapshot:
    return RefreshSnapshot(**{name: getattr(snapshot, name) for name in MEMBER_FIELDS})


def refresh_view(snapshot: RefreshSnapshot, role: str) -> RefreshSnapshot:
    return snapshot if role == "admin" else member_refresh_view(snapshot)
