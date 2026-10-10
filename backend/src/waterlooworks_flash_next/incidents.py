"""Fixed, secret-free incident lines for suppressed request failures.

A line carries only a random id, the method, the matched route template and the
exception type. Never the message, arguments, traceback, raw path, query,
headers, cookies or body.
"""

import secrets
import sys
from collections.abc import Mapping, MutableMapping
from contextvars import ContextVar, Token
from typing import Any

from fastapi import HTTPException

from waterlooworks_flash_next.logging import stage_logger

log = stage_logger("api")
# The router adds the matched route to this same scope mapping after the
# transport middleware binds it, so the template is known at failure time.
_scope: ContextVar[MutableMapping[str, Any] | None] = ContextVar(
    "ww_request_scope", default=None
)


def bind_request(scope: MutableMapping[str, Any]) -> Token[Any]:
    return _scope.set(scope)


def unbind_request(token: Token[Any]) -> None:
    _scope.reset(token)


def record_incident(
    kind: str, error: BaseException | None, scope: Mapping[str, Any] | None = None
) -> str:
    current = scope if scope is not None else _scope.get()
    incident = secrets.token_hex(4)
    method = "-" if current is None else str(current.get("method", "-"))
    route = (
        "unmatched"
        if current is None
        else getattr(current.get("route"), "path", "unmatched")
    )
    log.error(
        "%s request failure; incident %s; %s %s; %s",
        kind,
        incident,
        method,
        route,
        "unknown" if error is None else type(error).__name__,
    )
    return incident


def suppressed_failure(
    detail: str, *, status: int = 500, headers: dict[str, str] | None = None
) -> HTTPException:
    """Log the exception being handled and return a fixed-text HTTP error.

    Call inside an ``except`` block and raise the result ``from None``.
    """
    incident = record_incident("Suppressed", sys.exc_info()[1])
    return HTTPException(status, f"{detail} Incident {incident}.", headers=headers)
