"""Bounded live-update streams; counts only digests, never raw session cookies."""

import hashlib
import threading

PER_SESSION_STREAMS = 4
TOTAL_STREAMS = 32


def session_key(cookie: str | None) -> str:
    """Stable per-session stream key that never retains the bearer cookie."""
    return hashlib.sha256(b"ww-sse\0" + (cookie or "").encode()).hexdigest()


class StreamLease:
    """One admitted stream; releasing twice never frees another stream's slot."""

    def __init__(self, limiter: "StreamLimiter", key: str) -> None:
        self._limiter = limiter
        self._key = key
        self._released = False

    def release(self) -> None:
        self._limiter._release(self)


class StreamLimiter:
    """Admit at most a few streams per session and a fixed total per process."""

    def __init__(
        self, per_session: int = PER_SESSION_STREAMS, total: int = TOTAL_STREAMS
    ) -> None:
        self.per_session = per_session
        self.total = total
        self._lock = threading.Lock()
        self._counts: dict[str, int] = {}
        self._active = 0

    @property
    def active(self) -> int:
        with self._lock:
            return self._active

    def acquire(self, key: str) -> StreamLease | None:
        with self._lock:
            if (
                self._active >= self.total
                or self._counts.get(key, 0) >= self.per_session
            ):
                return None
            self._counts[key] = self._counts.get(key, 0) + 1
            self._active += 1
            return StreamLease(self, key)

    def _release(self, lease: StreamLease) -> None:
        with self._lock:
            if lease._released:
                return
            lease._released = True
            remaining = self._counts[lease._key] - 1
            if remaining:
                self._counts[lease._key] = remaining
            else:
                del self._counts[lease._key]
            self._active -= 1
