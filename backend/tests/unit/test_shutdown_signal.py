"""SIGTERM reaches the cooperative refresh stop even while an SSE stream is open.

Drives a real uvicorn server on loopback with an empty database and a real hashed
session; no posting records or platform responses are fabricated.
"""

import asyncio
import os
import signal
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import FrameType

import httpx
import pytest
import uvicorn

from waterlooworks_flash_next.api import create_app
from waterlooworks_flash_next.app_auth import (
    COOKIE,
    BootstrapOwner,
    SessionStore,
    require_firebase_ready,
)
from waterlooworks_flash_next.refresh import RefreshService
from waterlooworks_flash_next.shutdown import ShutdownNotice


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture
def swallowed_sigterm() -> Iterator[list[int]]:
    """uvicorn re-raises captured signals after shutdown; keep pytest alive."""
    received: list[int] = []

    def record(sig: int, frame: FrameType | None) -> None:
        received.append(sig)

    previous = signal.signal(signal.SIGTERM, record)
    try:
        yield received
    finally:
        signal.signal(signal.SIGTERM, previous)


def test_notice_chains_previous_handler_and_restores_it(
    swallowed_sigterm: list[int],
) -> None:
    calls: list[str] = []
    notice = ShutdownNotice()
    uninstall = notice.install(lambda: calls.append("stop"))
    try:
        signal.raise_signal(signal.SIGTERM)
        assert notice.is_set()
        assert calls == ["stop"]
        assert swallowed_sigterm == [signal.SIGTERM]
    finally:
        uninstall()
    signal.raise_signal(signal.SIGTERM)
    assert calls == ["stop"]
    assert swallowed_sigterm == [signal.SIGTERM, signal.SIGTERM]


def test_notice_off_main_thread_is_a_no_op() -> None:
    notice = ShutdownNotice()
    result: list[object] = []
    worker = threading.Thread(
        target=lambda: result.append(notice.install(lambda: None))
    )
    worker.start()
    worker.join()
    uninstall = result[0]
    assert callable(uninstall)
    uninstall()
    assert not notice.is_set()


def test_sigterm_with_open_sse_stream_requests_stop_and_ends_stream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    swallowed_sigterm: list[int],
) -> None:
    path = tmp_path / "db.sqlite3"
    port = _free_port()
    origin = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("WW_AUTH_ORIGINS", origin)
    monkeypatch.setenv("WW_AUTH_ALLOW_LOOPBACK_HTTP", "1")
    stops: list[float | None] = []
    original = RefreshService.request_stop

    def observed(self: RefreshService, timeout: float | None = 0) -> None:
        stops.append(timeout)
        original(self, timeout)

    monkeypatch.setattr(RefreshService, "request_stop", observed)

    app = create_app(path)
    app.dependency_overrides[require_firebase_ready] = lambda: None
    # Default config: no graceful-shutdown timeout, so only the app can end streams.
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    events: list[str] = []
    stream_ended = threading.Event()

    def client() -> None:
        while not server.started:
            time.sleep(0.05)
        store = SessionStore(path, bootstrap=BootstrapOwner(uid="internal-signal-test"))
        now = int(time.time())
        cookie = store.issue(
            store.prepare(None), "internal-signal-test", now + 3600, auth_time=now
        ).cookie
        headers = {
            "origin": origin,
            "x-ww-request": "1",
            "cookie": f"{COOKIE}={cookie}",
        }
        with httpx.stream(
            "GET", f"{origin}/api/refresh/events", headers=headers, timeout=20
        ) as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                events.append(line)
                if line.startswith("event: refresh") and len(events) < 3:
                    os.kill(os.getpid(), signal.SIGTERM)
        stream_ended.set()

    worker = threading.Thread(target=client, daemon=True)
    worker.start()
    started = time.monotonic()
    asyncio.run(asyncio.wait_for(server.serve(), timeout=20))
    worker.join(5)
    assert stream_ended.is_set()
    assert time.monotonic() - started < 15
    assert any(line.startswith("event: refresh") for line in events)
    # The signal itself requests a non-blocking stop before the lifespan shutdown.
    assert stops and stops[0] == 0
    assert swallowed_sigterm == [signal.SIGTERM]
