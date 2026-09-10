"""Cooperative process exclusion for one private local database, never a PID lock."""

import os
import sys
from pathlib import Path
from typing import BinaryIO


class ProviderBusy(Exception):
    """Safe admission failure before collection, Duo or paid dispatch."""


class ProviderLease:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve(strict=True)
        self._file: BinaryIO | None = None

    def acquire(self) -> "ProviderLease":
        if self._file is not None:
            raise ProviderBusy("Provider ownership is already held.")
        handle = None
        try:
            descriptor = os.open(
                str(self.path) + ".provider.lock", os.O_RDWR | os.O_CREAT, 0o600
            )
            handle = os.fdopen(descriptor, "r+b", buffering=0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._file = handle
            return self
        except Exception:
            if handle is not None:
                handle.close()
            raise ProviderBusy(
                "Collection or OpenAI work already owns this database."
            ) from None

    def require_owner(self, path: Path) -> None:
        if (
            self._file is None
            or self._file.closed
            or path.resolve(strict=True) != self.path
        ):
            raise ProviderBusy("Provider ownership is unavailable.")

    def close(self) -> None:
        if self._file is not None:
            # Closing releases the OS lock, including on process exit. Never unlink:
            # replacing its inode could permit two independently locked owners.
            self._file.close()
            self._file = None

    def __enter__(self) -> "ProviderLease":
        return self.acquire()

    def __exit__(self, *_: object) -> None:
        self.close()
