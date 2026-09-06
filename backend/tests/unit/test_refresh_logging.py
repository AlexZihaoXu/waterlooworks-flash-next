"""Generic log records only: no postings, authentication, or source responses."""

import ast
import logging
from pathlib import Path

from waterlooworks_flash_next.refresh import RefreshService, SafeRefreshHandler
from waterlooworks_flash_next.refresh_logging import FORMATS


def record(message: str, *args: object, level: int = logging.INFO) -> logging.LogRecord:
    return logging.LogRecord("login", level, "", 0, message, args, None)


def test_actual_messages_and_all_emitted_levels(tmp_path: Path) -> None:
    service = RefreshService(tmp_path / "unused.sqlite3", None)
    handler = SafeRefreshHandler(service)
    handler.emit(record("Starting stage %d/%d: %s", 1, 10, "start window"))
    handler.emit(
        record("Waiting %d ms before the next stage.", 1000, level=logging.DEBUG)
    )
    handler.emit(
        record(
            "Browser cleanup did not complete; diagnostics suppressed.",
            level=logging.WARNING,
        )
    )
    logs = service.snapshot().logs
    assert logs[0].message == "[login] Starting stage 1/10: start window"
    assert logs[1].message == "[login] Waiting 1000 ms before the next stage."
    assert logs[1].level == "debug"
    assert logs[2].level == "warning"
    assert "Browser cleanup did not complete" in logs[2].message
    assert [log.sequence for log in logs] == [1, 2, 3]


def test_sensitive_and_unreviewed_content_never_formatted(tmp_path: Path) -> None:
    service = RefreshService(tmp_path / "unused.sqlite3", None)
    handler = SafeRefreshHandler(service)
    marker = "PRIVATE_TEST_SENTINEL"
    handler.emit(record("Detected verification code: [%s] (waiting...)", marker))
    handler.emit(record("arbitrary unreviewed: %s", marker))
    handler.emit(record("Starting stage %d/%d: %s", 1, 10, marker))
    dangerous = record("Password entry complete; returning control to the caller.")
    dangerous.exc_text = marker
    dangerous.stack_info = marker
    handler.emit(dangerous)
    logs = service.snapshot().logs
    assert len(logs) == 4
    assert marker not in service.snapshot().model_dump_json()
    assert "Duo approval required" in logs[0].message
    assert "[redacted]" in logs[2].message


def test_full_page_message_preserves_eta_and_updates_progress(tmp_path: Path) -> None:
    service = RefreshService(tmp_path / "unused.sqlite3", None)
    handler = SafeRefreshHandler(service)
    event = logging.LogRecord(
        "listings",
        logging.INFO,
        "",
        0,
        "Fetch finished: page %d/%d | received %d | records %d | "
        "unique %d | duplicates %d | ETA %s%s",
        (0, 0, 0, 0, 0, 0, "estimating", ""),
        None,
    )
    handler.emit(event)
    snapshot = service.snapshot()
    assert snapshot.total_pages == 0
    assert snapshot.logs[0].message == "[listings] " + event.getMessage()
    assert "ETA estimating" in snapshot.logs[0].message


def test_eta_uses_only_valid_emitted_duration_arguments(tmp_path: Path) -> None:
    from waterlooworks_flash_next.refresh_logging import PAGE_FORMAT

    service = RefreshService(tmp_path / "unused.sqlite3", None)
    handler = SafeRefreshHandler(service)
    for value, expected in [
        ("00:01:30", 90),
        ("estimating", None),
        ("PRIVATE_TEST_SENTINEL", None),
    ]:
        handler.emit(
            logging.LogRecord(
                "listings",
                logging.INFO,
                "",
                0,
                PAGE_FORMAT,
                (1, 10, 0, 0, 0, 0, value, ""),
                None,
            )
        )
        assert service.snapshot().eta_seconds == expected
    assert "PRIVATE_TEST_SENTINEL" not in service.snapshot().model_dump_json()


def test_every_current_stage_log_format_is_reviewed() -> None:
    root = Path(__file__).parents[2] / "src" / "waterlooworks_flash_next"
    for module in ("login", "duo", "jobs", "listings"):
        tree = ast.parse((root / f"{module}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"debug", "info", "warning", "error", "critical"}
            ):
                assert node.args and isinstance(node.args[0], ast.Constant)
                assert node.args[0].value in FORMATS | {
                    "Detected verification code: [%s] (waiting...)"
                }


def test_unknown_objects_are_not_stringified_and_buffer_remains_bounded(
    tmp_path: Path,
) -> None:
    class Unprintable:
        def __str__(self) -> str:
            raise AssertionError("Private arguments must not be formatted")

    service = RefreshService(tmp_path / "unused.sqlite3", None)
    handler = SafeRefreshHandler(service)
    handler.emit(
        record(
            "Failed stage: %s. Sensitive diagnostics suppressed.",
            Unprintable(),
            level=logging.CRITICAL,
        )
    )
    assert service.snapshot().logs[-1].level == "critical"
    assert "[redacted]" in service.snapshot().logs[-1].message
    for _ in range(201):
        handler.emit(record("Waiting %d ms before the next stage.", 0))
    snapshot = service.snapshot()
    assert len(snapshot.logs) == 200
    assert snapshot.logs[-1].sequence == 202
