"""Run with uv's Python -i; the browser remains open at the password checkpoint."""

from pathlib import Path

from waterlooworks_flash_next.logging import stage_logger
from waterlooworks_flash_next.login import LoginFlowError, start_login

log = stage_logger("shell")

try:
    session = start_login(Path(__file__).resolve().parents[2] / ".credentials.json")
    page = session.page
    log.info("Use page for navigation; session.close() to close.")
except LoginFlowError:
    log.error("Login flow stopped. Sensitive diagnostics suppressed.")
