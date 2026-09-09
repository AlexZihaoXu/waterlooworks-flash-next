"""Primitive request-policy/configuration tests; no posting/API responses."""

from collections.abc import Callable
from types import SimpleNamespace
from typing import cast

import pytest
from playwright.sync_api import Page, Route

from waterlooworks_flash_next.detail_capture import CaptureGate, install_capture_guard
from waterlooworks_flash_next.detail_fetch import DetailFetchError, DetailSource
from waterlooworks_flash_next.listings import LISTING_URL
from waterlooworks_flash_next.login import service_worker_policy


@pytest.mark.parametrize("extra", ["&extra=1", "&extra=", "&%65xtra=1"])
def test_extra_form_fields_never_reach_transport(extra: str) -> None:
    # Primitive route controls only; no platform response or posting fixture.
    handlers: list[Callable[[Route], None]] = []
    calls: list[str] = []
    frame = object()

    def register(pattern: str, handler: Callable[[Route], None]) -> None:
        assert pattern == "**/*"
        handlers.append(handler)

    def request(operation: Callable[[], str]) -> str:
        calls.append("request")
        return operation()

    page = cast(
        Page,
        SimpleNamespace(
            main_frame=frame,
            context=SimpleNamespace(route=register),
        ),
    )
    gate = install_capture_guard(
        page, "1", request=request, consume=lambda text, gate: None
    )
    route = cast(
        Route,
        SimpleNamespace(
            request=SimpleNamespace(
                url=LISTING_URL,
                method="POST",
                post_data="postingId=1&action=control" + extra,
                frame=frame,
                resource_type="xhr",
            ),
            abort=lambda: calls.append("abort"),
        ),
    )
    handlers[0](route)
    assert gate.invalid and gate.templates == []
    assert calls == ["abort"]


def test_late_unexpected_request_invalidates_runtime_source_before_collection() -> None:
    gate = CaptureGate("1")
    source = DetailSource((), {}, (), capture_check=lambda: not gate.invalid)
    source.confirm_capture()
    gate.observe(
        "https://example.invalid/late",
        "GET",
        "",
        main_frame=False,
        resource_type="document",
    )
    with pytest.raises(DetailFetchError):
        source.confirm_capture()


def test_service_workers_are_blocked_only_for_explicit_capture_mode() -> None:
    assert service_worker_policy(True) == "block"
    assert service_worker_policy(False) == "allow"


@pytest.mark.parametrize(
    "url,method,main_frame,kind",
    [
        (LISTING_URL, "GET", True, "fetch"),
        (LISTING_URL + "?alternate=1", "POST", True, "xhr"),
        ("https://example.invalid/other", "POST", True, "xhr"),
        (LISTING_URL, "POST", False, "xhr"),
        (LISTING_URL, "POST", True, "document"),
    ],
)
def test_unexpected_requests_are_invalid_even_with_matching_form(
    url: str,
    method: str,
    main_frame: bool,
    kind: str,
) -> None:
    gate = CaptureGate("1")
    gate.observe(
        url,
        method,
        "postingId=1&action=control",
        main_frame=main_frame,
        resource_type=kind,
    )
    assert gate.invalid
    assert gate.templates == []


def test_only_two_distinct_expected_forms_are_recorded_and_all_else_fails_closed() -> (
    None
):
    gate = CaptureGate("1")
    for action in ("control-a", "control-b"):
        gate.observe(
            LISTING_URL,
            "POST",
            "postingId=1&action=" + action,
            main_frame=True,
            resource_type="xhr",
        )
    assert not gate.invalid and len(gate.templates) == 2
    gate.observe(
        LISTING_URL,
        "POST",
        "postingId=1&action=extra",
        main_frame=True,
        resource_type="xhr",
    )
    assert gate.invalid and len(gate.templates) == 2


@pytest.mark.parametrize("state", ["active", "complete", "invalid"])
def test_bootstrap_never_allows_egress_after_completion_or_during_another_request(
    state: str,
) -> None:
    gate = CaptureGate("1")
    setattr(gate, state, True)
    gate.observe(
        LISTING_URL,
        "POST",
        "postingId=1&action=control",
        main_frame=True,
        resource_type="xhr",
    )
    assert gate.invalid
    assert gate.templates == []


@pytest.mark.parametrize(
    "kind", ["image", "font", "stylesheet", "media", "texttrack", "script"]
)
@pytest.mark.parametrize("phase", ["idle", "active", "complete"])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_passive_routes_are_denied_without_transport_or_capture_poisoning(
    kind: str, phase: str, method: str
) -> None:
    handlers: list[Callable[[Route], None]] = []
    calls: list[str] = []
    frame = object()
    page = cast(
        Page,
        SimpleNamespace(
            main_frame=frame,
            context=SimpleNamespace(
                route=lambda pattern, handler: handlers.append(handler)
            ),
        ),
    )

    def request(operation: Callable[[], str]) -> str:
        calls.append("request")
        return operation()

    gate = install_capture_guard(
        page, "1", request=request, consume=lambda text, gate: calls.append("consume")
    )
    gate.active = phase == "active"
    gate.complete = phase == "complete"
    source = DetailSource((), {}, (), capture_check=lambda: not gate.invalid)
    route = cast(
        Route,
        SimpleNamespace(
            request=SimpleNamespace(
                url="https://example.invalid/resource",
                method=method,
                post_data="",
                frame=frame,
                resource_type=kind,
            ),
            abort=lambda: calls.append("abort"),
            fetch=lambda **kwargs: calls.append("fetch"),
        ),
    )
    handlers[0](route)
    assert calls == ["abort"]
    assert gate.templates == []
    assert gate.invalid == (method == "POST")
    assert gate.active == (phase == "active")
    if method == "GET":
        source.confirm_capture()
        assert gate.unexpected == 0
    else:
        with pytest.raises(DetailFetchError):
            source.confirm_capture()


def test_passive_request_cannot_repair_invalid_gate() -> None:
    gate = CaptureGate("1", invalid=True)
    gate.observe("", "GET", "", main_frame=False, resource_type="image")
    assert gate.invalid and gate.templates == []


@pytest.mark.parametrize("kind", ["document", "xhr", "fetch", "other"])
@pytest.mark.parametrize("phase", ["idle", "active", "complete"])
def test_nonpassive_get_is_always_fatal(kind: str, phase: str) -> None:
    gate = CaptureGate("1", active=phase == "active", complete=phase == "complete")
    assert not gate.observe(LISTING_URL, "GET", "", main_frame=True, resource_type=kind)
    assert gate.invalid and gate.templates == []


@pytest.mark.parametrize("inspection_failure", [False, True])
def test_passive_classification_precedes_private_request_inspection(
    inspection_failure: bool,
) -> None:
    handlers: list[Callable[[Route], None]] = []
    calls: list[str] = []
    diagnostics: list[str] = []
    page = cast(
        Page,
        SimpleNamespace(
            context=SimpleNamespace(
                route=lambda pattern, handler: handlers.append(handler)
            )
        ),
    )
    gate = install_capture_guard(page, "1", blocked_report=diagnostics.append)
    # Missing URL/body/frame must not be inspected for a passive GET. Missing
    # method instead is an inspection failure, not a background exemption.
    incoming = SimpleNamespace(resource_type="image")
    if not inspection_failure:
        incoming.method = "GET"
    route = cast(
        Route, SimpleNamespace(request=incoming, abort=lambda: calls.append("abort"))
    )
    handlers[0](route)
    assert calls == ["abort"] and gate.templates == []
    assert gate.invalid == inspection_failure
    assert len(diagnostics) == 1
    assert (
        "disposition=fatal" if inspection_failure else "disposition=discarded"
    ) in diagnostics[0]
    if inspection_failure:
        assert gate.reason.value == "request_inspection"


def test_abort_failure_is_fatal_and_safely_reported() -> None:
    handlers: list[Callable[[Route], None]] = []
    diagnostics: list[str] = []
    page = cast(
        Page,
        SimpleNamespace(
            context=SimpleNamespace(
                route=lambda pattern, handler: handlers.append(handler)
            )
        ),
    )
    gate = install_capture_guard(page, "1", blocked_report=diagnostics.append)

    def abort() -> None:
        raise RuntimeError("secret transport text")

    route = cast(
        Route,
        SimpleNamespace(
            request=SimpleNamespace(resource_type="image", method="GET"), abort=abort
        ),
    )
    handlers[0](route)
    assert gate.invalid and gate.reason.value == "request_inspection"
    assert "disposition=fatal" in diagnostics[0] and "secret" not in diagnostics[0]


def test_passive_arrival_inside_fetch_preserves_active_owner_and_ledger_boundary() -> (
    None
):
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    handlers: list[Callable[[Route], None]] = []
    calls: list[str] = []
    frame = object()
    page = cast(
        Page,
        SimpleNamespace(
            main_frame=frame,
            context=SimpleNamespace(
                route=lambda pattern, handler: handlers.append(handler)
            ),
        ),
    )

    def request(operation: Callable[[], str]) -> str:
        calls.append("ledger/request")
        return operation()

    gate = install_capture_guard(
        page, "1", request=request, consume=lambda text, gate: None
    )
    background = cast(
        Route,
        SimpleNamespace(
            request=SimpleNamespace(resource_type="image", method="GET"),
            abort=lambda: calls.append("background-abort"),
        ),
    )

    def fetch(**kwargs: object) -> None:
        assert kwargs == {"max_redirects": 0, "max_retries": 0, "timeout": 30000}
        calls.append("fetch")
        handlers[0](background)
        assert gate.active and not gate.invalid and len(gate.templates) == 1
        # Transport control failure only; no fabricated response body.
        raise PlaywrightTimeoutError("private transport diagnostics")

    route = cast(
        Route,
        SimpleNamespace(
            request=SimpleNamespace(
                url=LISTING_URL,
                resource_type="xhr",
                method="POST",
                post_data="postingId=1&action=control",
                frame=frame,
            ),
            fetch=fetch,
            abort=lambda: calls.append("parent-abort"),
        ),
    )
    handlers[0](route)
    assert calls == ["ledger/request", "fetch", "background-abort", "parent-abort"]
    assert not gate.active and gate.error is not None
