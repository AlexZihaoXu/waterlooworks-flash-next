"""SSE loop and stream-cap contracts on real empty databases; no posting fixtures."""

import asyncio
import threading
from pathlib import Path
from typing import NoReturn

from auth_support import session_client
from fastapi import Request

from waterlooworks_flash_next.api import create_app, session_event_stream
from waterlooworks_flash_next.api_models import RefreshSnapshot
from waterlooworks_flash_next.app_auth import COOKIE, SessionIdentity, SessionStore
from waterlooworks_flash_next.event_streams import StreamLimiter, session_key
from waterlooworks_flash_next.refresh import RefreshService


def test_stream_limiter_caps_per_session_and_globally() -> None:
    limiter = StreamLimiter(per_session=2, total=3)
    first = limiter.acquire("a")
    second = limiter.acquire("a")
    assert first is not None and second is not None
    assert limiter.acquire("a") is None
    third = limiter.acquire("b")
    assert third is not None
    assert limiter.acquire("c") is None
    first.release()
    first.release()  # Idempotent: a double release never frees another stream.
    assert limiter.active == 2
    replacement = limiter.acquire("a")
    assert replacement is not None
    assert limiter.acquire("c") is None
    for lease in (second, third, replacement):
        lease.release()
    assert limiter.active == 0


def test_session_key_is_a_digest_not_the_cookie() -> None:
    assert session_key("cookie-value") != "cookie-value"
    assert len(session_key("cookie-value")) == 64
    assert session_key(None) == session_key(None)


def test_stream_ticks_run_blocking_reads_off_the_event_loop(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    threads: dict[str, int] = {}

    class ObservedSessions(SessionStore):
        def lookup(
            self, cookie: str | None, *, now: int | None = None
        ) -> SessionIdentity | None:
            threads["lookup"] = threading.get_ident()
            return super().lookup(cookie, now=now)

    class ObservedService(RefreshService):
        def snapshot(self) -> RefreshSnapshot:
            threads["snapshot"] = threading.get_ident()
            return super().snapshot()

    with session_client(path, app=create_app(path)) as client:
        cookie = client.cookies.get(COOKIE)

        async def receive() -> NoReturn:
            await asyncio.Future()
            raise AssertionError("Unreachable")

        request = Request(
            {"type": "http", "headers": [(b"cookie", f"{COOKIE}={cookie}".encode())]},
            receive=receive,
        )

        async def consume() -> int:
            stream = session_event_stream(
                request, ObservedService(path, None), ObservedSessions(path), once=True
            )
            assert "event: refresh" in await anext(stream)
            await stream.aclose()
            return threading.get_ident()

        loop_thread = asyncio.run(consume())
    assert set(threads) == {"lookup", "snapshot"}
    assert loop_thread not in threads.values()


def test_event_endpoint_refuses_streams_beyond_the_session_cap(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    app = create_app(path)
    with session_client(path, app=app) as client:
        limiter: StreamLimiter = app.state.event_streams
        key = session_key(client.cookies.get(COOKIE))
        held = [limiter.acquire(key) for _ in range(limiter.per_session)]
        assert all(lease is not None for lease in held)
        refused = client.get("/api/refresh/events?once=true")
        assert refused.status_code == 429
        assert refused.headers["retry-after"]
        assert refused.json()["detail"] == (
            "Too many live update streams are open; close another tab and retry."
        )
        held[0].release()  # type: ignore[union-attr]
        accepted = client.get("/api/refresh/events?once=true")
        assert accepted.status_code == 200
        assert "event: refresh" in accepted.text
        # A finished stream returns its slot.
        assert limiter.active == limiter.per_session - 1
        for lease in held[1:]:
            lease.release()  # type: ignore[union-attr]
        assert limiter.active == 0
