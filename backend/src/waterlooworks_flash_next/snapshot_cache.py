"""One in-memory snapshot, invalidated by commits on a persistent SQLite observer."""

import sqlite3
from collections.abc import Callable
from pathlib import Path
from threading import Lock


class SnapshotCache[T]:
    """Serialize loads; never reuse a payload after an observed database change."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self._lock = Lock()
        self._connection: sqlite3.Connection | None = None
        self._identity: tuple[int, int] | None = None
        self._version: int | None = None
        self._value: T | None = None

    def _observe(self) -> int:
        stat = self.path.stat()
        identity = (stat.st_dev, stat.st_ino)
        if self._connection is None or identity != self._identity:
            if self._connection is not None:
                self._connection.close()
            self._connection = sqlite3.connect(
                self.path.as_uri() + "?mode=ro", uri=True, check_same_thread=False
            )
            self._connection.execute("PRAGMA query_only=ON")
            self._identity = identity
            self._version = None
            self._value = None
        return int(self._connection.execute("PRAGMA data_version").fetchone()[0])

    def get(self, load: Callable[[], T]) -> T:
        """Return a cached snapshot or load once; concurrent commits prevent caching."""
        with self._lock:
            before = self._observe()
            identity = self._identity
            if self._value is not None and before == self._version:
                return self._value
            self._value = None
            self._version = None
            value = load()
            after = self._observe()
            if identity == self._identity and before == after:
                self._value = value
                self._version = after
            return value

    def close(self) -> None:
        """Release the observer and all saved payload data."""
        with self._lock:
            if self._connection is not None:
                self._connection.close()
            self._connection = None
            self._value = None
            self._version = None
