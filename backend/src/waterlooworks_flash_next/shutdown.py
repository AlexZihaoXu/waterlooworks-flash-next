"""Observe SIGTERM/SIGINT independently of the ASGI lifespan ordering.

uvicorn sends the lifespan shutdown only after every in-flight response ends, and
a live SSE stream never ends on its own. Chaining the server's signal handlers lets
the refresh worker and open streams learn about shutdown when the signal arrives.
"""

import signal
import threading
from collections.abc import Callable
from types import FrameType
from typing import Any

HANDLED_SIGNALS = (signal.SIGTERM, signal.SIGINT)


class ShutdownNotice:
    def __init__(self) -> None:
        self._event = threading.Event()

    def is_set(self) -> bool:
        return self._event.is_set()

    def install(self, on_signal: Callable[[], None]) -> Callable[[], None]:
        """Chain a handler in front of the current ones; return an uninstaller.

        Signals can only be handled on the main thread, so elsewhere (tests, embedded
        servers) this is a no-op and shutdown falls back to the lifespan path.
        """
        if threading.current_thread() is not threading.main_thread():
            return lambda: None
        previous: dict[int, Any] = {}

        def handle(sig: int, frame: FrameType | None) -> None:
            self._event.set()
            try:
                on_signal()
            finally:
                chained = previous.get(sig)
                if callable(chained):
                    chained(sig, frame)
                elif chained == signal.SIG_DFL:
                    # Preserve default termination when nothing else handled it.
                    signal.signal(sig, signal.SIG_DFL)
                    signal.raise_signal(sig)

        for sig in HANDLED_SIGNALS:
            previous[sig] = signal.signal(sig, handle)

        def uninstall() -> None:
            for sig, handler in previous.items():
                if signal.getsignal(sig) is handle:
                    signal.signal(sig, handler)

        return uninstall
