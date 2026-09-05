"""Minecraft-style console logs for fixed, non-sensitive stage messages only."""

import logging
import os
import sys


class StageFormatter(logging.Formatter):
    """Render time, scope, level and message; never append exception diagnostics.

    This is not a secret scrubber. Callers must never supply sensitive messages.
    """

    def __init__(self, *, color: bool) -> None:
        super().__init__()
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        timestamp = self.formatTime(record, "%H:%M:%S")
        level = "WARN" if record.levelno == logging.WARNING else record.levelname
        message = record.getMessage()
        if not self.color:
            return f"[{timestamp}] [{record.name}/{level}]: {message}"
        colors = {
            logging.DEBUG: "\x1b[90m",
            logging.INFO: "\x1b[92m",
            logging.WARNING: "\x1b[93m",
            logging.ERROR: "\x1b[91m",
            logging.CRITICAL: "\x1b[91m",
        }
        reset = "\x1b[0m"
        color = colors.get(record.levelno, "")
        return (
            f"\x1b[90m[{timestamp}]{reset} "
            f"[\x1b[96m{record.name}{reset}/{color}{level}{reset}]: {message}"
        )


def stage_logger(scope: str) -> logging.Logger:
    """Configure only the named logger, without exposing third-party debug logs."""
    logger = logging.getLogger(scope)
    logger.propagate = False
    if not logger.handlers:
        color = "NO_COLOR" not in os.environ and (
            sys.stdout.isatty() or os.environ.get("FORCE_COLOR", "0") != "0"
        )
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StageFormatter(color=color))
        logger.addHandler(handler)
    level = os.environ.get("WW_LOG_LEVEL", "INFO").upper()
    logger.setLevel(
        {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARN": logging.WARNING,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
        }.get(level, logging.INFO)
    )
    return logger
