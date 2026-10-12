"""Small in-process fixed-window request limiter keyed by an opaque string.

Keys are digests or UIDs, never raw tokens. Memory is bounded: expired
windows are dropped on every call and the oldest window is evicted once the
key cap is reached, so a flood of distinct keys cannot grow without limit.
"""

import math
import threading
import time


class RequestLimiter:
    def __init__(
        self, *, limit: int, window_seconds: float, max_keys: int = 10_000
    ) -> None:
        if limit < 1 or window_seconds <= 0 or max_keys < 1:
            raise ValueError("Limiter bounds must be positive.")
        self.limit = limit
        self.window = float(window_seconds)
        self.max_keys = max_keys
        self._lock = threading.Lock()
        # key -> (window start, count); insertion order doubles as age order.
        self._windows: dict[str, tuple[float, int]] = {}

    @property
    def tracked_keys(self) -> int:
        with self._lock:
            return len(self._windows)

    def admit(self, key: str, *, now: float | None = None) -> int | None:
        """Count one request; None when admitted, else whole seconds to wait."""
        now = time.monotonic() if now is None else now
        with self._lock:
            for stale in [
                k
                for k, (start, _) in self._windows.items()
                if start + self.window <= now
            ]:
                del self._windows[stale]
            start, count = self._windows.get(key, (now, 0))
            if count >= self.limit:
                return max(1, math.ceil(start + self.window - now))
            if key not in self._windows and len(self._windows) >= self.max_keys:
                del self._windows[next(iter(self._windows))]
            self._windows[key] = (start, count + 1)
            return None
