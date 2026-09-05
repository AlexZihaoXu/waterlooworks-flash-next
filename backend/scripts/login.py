"""Run with uv's Python -i; submit once, observe Duo, pause on unknown branches."""

from pathlib import Path

from waterlooworks_flash_next.duo import submit_and_observe_duo
from waterlooworks_flash_next.logging import stage_logger
from waterlooworks_flash_next.login import LoginFlowError, start_login

log = stage_logger("shell")

try:
    session = start_login(Path(__file__).resolve().parents[2] / ".credentials.json")
    page = session.page
    submit_and_observe_duo(session)
    log.info("Use page for navigation; session.close() to close.")
except LoginFlowError:
    log.error("Login flow stopped. Sensitive diagnostics suppressed.")
