"""Backend-only bounded secret input; never copy mounted values into environment."""

import os
import stat
import threading
from pathlib import Path

from dotenv import dotenv_values

# Values parsed from the ignored local .env. They live only in this private
# mapping and are never exported to os.environ, so child processes (the
# Playwright driver and Chromium) cannot inherit them.
_local_environment: dict[str, str] = {}
_local_lock = threading.Lock()


class RuntimeSecretError(Exception):
    """Fixed diagnostic only, without file names or input values."""


def load_local_environment(path: Path) -> None:
    """Parse one explicit local env file into the private lookup.

    Process environment variables keep precedence per name. A missing file is
    an empty mapping. Callers load at explicit startup/login points only.
    """
    try:
        values = dotenv_values(path) if path.exists() else {}
        parsed = {key: value for key, value in values.items() if value is not None}
    except Exception:
        raise RuntimeSecretError("Runtime configuration unavailable.") from None
    with _local_lock:
        _local_environment.clear()
        _local_environment.update(parsed)


def clear_local_environment() -> None:
    with _local_lock:
        _local_environment.clear()


def runtime_value(name: str) -> str | None:
    """Process environment first, then the private local env lookup."""
    value = os.environ.get(name)
    if value is not None:
        return value
    with _local_lock:
        return _local_environment.get(name)


def read_runtime_secret(name: str, *, max_bytes: int) -> str | None:
    """Read env OR an explicit regular file with at most one terminal newline.

    Files contain the same encoding as local env values (including Firebase's
    canonical base64). No implicit discovery, environment mutation or fallback.
    """
    try:
        value = runtime_value(name)
        path = runtime_value(name + "_FILE")
        if value is not None and path is not None:
            raise ValueError
        if path is None:
            if value is not None and len(value.encode("utf-8")) > max_bytes:
                raise ValueError
            return value
        if not path or not os.path.isabs(path):
            raise ValueError
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes + 2:
                raise ValueError
            raw = stream.read(max_bytes + 3)
        raw = raw.removesuffix(b"\n").removesuffix(b"\r")
        if not raw or len(raw) > max_bytes:
            raise ValueError
        return raw.decode("utf-8")
    except Exception:
        raise RuntimeSecretError("Runtime secret unavailable.") from None
