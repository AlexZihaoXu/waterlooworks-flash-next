"""One in-memory snapshot, invalidated by saved-data writes seen by an observer."""

import sqlite3
from collections.abc import Callable
from pathlib import Path
from threading import Lock

from waterlooworks_flash_next.data_generation import read_generation


class SnapshotCache[T]:
    """Serialize loads; never reuse a payload after an observed saved-data change.

    The key is the trigger-maintained saved-data generation, so session and other
    excluded writes keep the snapshot; it degrades to ``PRAGMA data_version`` (any
    commit) whenever the trigger set is incomplete.
    """

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self._lock = Lock()
        self._connection: sqlite3.Connection | None = None
        self._identity: tuple[int, int] | None = None
        self._version: tuple[str, int] | None = None
        self._value: T | None = None

    def _observe(self) -> tuple[str, int]:
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
        return read_generation(self._connection)

    def get(self, load: Callable[[], T]) -> T:
        """Return a cached snapshot or load once; concurrent writes prevent caching."""
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
