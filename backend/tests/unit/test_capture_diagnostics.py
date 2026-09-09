"""Generic hostile inputs must never appear in capture diagnostic text."""

from typing import cast

import pytest

from waterlooworks_flash_next.detail_capture import (
    CaptureGate,
    CaptureReason,
    CaptureStage,
    capture_diagnostic,
)


def test_diagnostics_expose_only_permanent_reason_and_counts() -> None:
    gate = CaptureGate("private-identity")
    gate.observe(
        "https://private.invalid/secret-url",
        "private-method",
        "action=secret-action&postingId=private-identity",
        main_frame=False,
        resource_type="private-resource",
    )
    gate.observe(
        "another-secret-url",
        "GET",
        "cookie=secret-cookie",
        main_frame=True,
        resource_type="xhr",
    )
    result = capture_diagnostic(
        CaptureStage.GUARD, 0, len(gate.templates), gate.reason, gate.unexpected
    )
    assert "reason=destination" in result and "unexpected=2" in result
    for forbidden in ("private", "secret", "cookie", "https", "identity"):
        assert forbidden not in result
    assert gate.reason == CaptureReason.DESTINATION


@pytest.mark.parametrize("counter", [True, -1, 100001, "secret-counter", float("nan")])
def test_each_counter_rejects_unreviewed_values(counter: object) -> None:
    for index in range(3):
        counts = [0, 0, 0]
        counts[index] = cast(int, counter)
        assert (
            capture_diagnostic(
                CaptureStage.SELECT, counts[0], counts[1], CaptureReason.NONE, counts[2]
            )
            == "Detail capture diagnostic: invalid diagnostic withheld."
        )


def test_every_stage_and_reason_has_only_enumerated_diagnostics() -> None:
    for stage in CaptureStage:
        for reason in CaptureReason:
            assert capture_diagnostic(stage, 1, 2, reason, 3) == (
                f"Detail capture: stage={stage.value}; title_matches=1; "
                f"templates=2; reason={reason.value}; unexpected=3."
            )


def test_invalid_diagnostic_inputs_are_withheld_without_echo() -> None:
    assert capture_diagnostic(
        cast(CaptureStage, "secret-stage"), 1, 0, CaptureReason.NONE, 0
    ).endswith("withheld.")
    assert capture_diagnostic(
        CaptureStage.CLICK, -1, 0, CaptureReason.NONE, 0
    ).endswith("withheld.")
    assert capture_diagnostic(CaptureStage.WAIT, 1, 1, CaptureReason.COUNT, 0) == (
        "Detail capture: stage=wait_templates; title_matches=1; "
        "templates=1; reason=template_count; unexpected=0."
    )


def test_blocked_diagnostics_and_log_allowlist_are_fixed_and_bounded() -> None:
    import logging

    from waterlooworks_flash_next.detail_capture import (
        BlockDisposition,
        CapturePhase,
        MethodClass,
        ResourceKind,
        blocked_diagnostic,
    )
    from waterlooworks_flash_next.refresh_logging import safe_message

    def safe(message: str) -> str:
        return safe_message(
            logging.LogRecord("refresh", logging.INFO, "", 0, message, (), None)
        )

    for kind in ResourceKind:
        for method in MethodClass:
            for phase in CapturePhase:
                for disposition in BlockDisposition:
                    text = blocked_diagnostic(kind, method, phase, disposition, 100000)
                    assert safe(text) == "[refresh] " + text
    text = blocked_diagnostic(
        ResourceKind.IMAGE,
        MethodClass.GET,
        CapturePhase.ACTIVE,
        BlockDisposition.DISCARDED,
        1,
    )
    for hostile in (
        text + "secret",
        text.replace("image", "secret"),
        text.replace("get", "secret"),
        text.replace("active", "secret"),
        text.replace("count=1", "count=100001"),
        text.replace("count=1", "count=-1"),
    ):
        assert "withheld" in safe(hostile) and "secret" not in safe(hostile)
    for counter in (-1, 100001, True):
        assert blocked_diagnostic(
            ResourceKind.IMAGE,
            MethodClass.GET,
            CapturePhase.IDLE,
            BlockDisposition.DISCARDED,
            counter,
        ).endswith("withheld.")
    assert blocked_diagnostic(
        cast(ResourceKind, "secret"),
        MethodClass.GET,
        CapturePhase.IDLE,
        BlockDisposition.DISCARDED,
        0,
    ).endswith("withheld.")
