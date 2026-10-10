"""Authenticated replays must never route through environment-configured proxies."""

from typing import Any
from urllib.request import ProxyHandler, build_opener

import pytest

from waterlooworks_flash_next import detail_fetch, listings


def test_replay_openers_ignore_proxy_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("https_proxy", "http://proxy.invalid:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    opener: Any = listings.build_direct_opener()
    # build_opener drops its env-reading default when given any ProxyHandler, and an
    # empty one registers no *_open hooks, so no proxy handler may remain.
    assert not any(isinstance(h, ProxyHandler) for h in opener.handlers)
    assert any(isinstance(h, listings.NoRedirect) for h in opener.handlers)
    default: Any = build_opener()
    assert any(isinstance(h, ProxyHandler) for h in default.handlers)


def test_listing_and_detail_replays_share_one_opener_factory() -> None:
    assert detail_fetch.build_direct_opener is listings.build_direct_opener  # type: ignore[attr-defined]
