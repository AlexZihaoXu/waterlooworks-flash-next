"""Primitive delivery callbacks only; no posting or HTTP response fixtures."""

from collections.abc import Callable
from types import SimpleNamespace
from typing import cast

from playwright.sync_api import Page, Route

from waterlooworks_flash_next.detail_capture import (
    forward_bootstrap_metadata,
    install_capture_guard,
)
from waterlooworks_flash_next.listings import LISTING_URL


def test_metadata_is_forwarded_once_to_allow_the_dependent_read() -> None:
    calls: list[str] = []
    forward_bootstrap_metadata(False, lambda: calls.append("forward"))
    assert calls == ["forward"]


def test_completed_content_is_not_rendered_in_the_browser() -> None:
    calls: list[str] = []
    forward_bootstrap_metadata(True, lambda: calls.append("forward"))
    assert calls == []


def test_completed_handler_leaves_route_pending_but_blocks_later_traffic() -> None:
    handlers: list[Callable[[Route], None]] = []
    calls: list[str] = []
    frame = object()

    def register(pattern: str, handler: Callable[[Route], None]) -> None:
        assert pattern == "**/*"
        handlers.append(handler)

    def completed_request(_operation: Callable[[], str]) -> str:
        # Controller completion signal only; no HTTP response or posting fixture.
        gate.complete = True
        return ""

    page = cast(
        Page,
        SimpleNamespace(
            main_frame=frame,
            context=SimpleNamespace(route=register),
        ),
    )
    gate = install_capture_guard(
        page,
        "1",
        request=completed_request,
        consume=lambda text, current: None,
    )
    incoming = SimpleNamespace(
        url=LISTING_URL,
        method="POST",
        post_data="postingId=1&action=control",
        frame=frame,
        resource_type="xhr",
    )
    route = cast(
        Route,
        SimpleNamespace(
            request=incoming,
            abort=lambda: calls.append("abort"),
            fulfill=lambda **kwargs: calls.append("fulfill"),
        ),
    )
    handlers[0](route)
    assert gate.complete and not gate.invalid and not gate.active
    assert calls == []
    incoming.url = "https://example.invalid/blocked"
    incoming.method = "GET"
    handlers[0](route)
    assert gate.invalid
    assert calls == ["abort"]
