"""Backend-only bounded secret input; never copy mounted values into environment."""

import os
import stat


class RuntimeSecretError(Exception):
    """Fixed diagnostic only, without file names or input values."""


def read_runtime_secret(name: str, *, max_bytes: int) -> str | None:
    """Read env OR an explicit regular file with at most one terminal newline.

    Files contain the same encoding as local env values (including Firebase's
    canonical base64). No implicit discovery, environment mutation or fallback.
    """
    try:
        value = os.environ.get(name)
        path = os.environ.get(name + "_FILE")
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
