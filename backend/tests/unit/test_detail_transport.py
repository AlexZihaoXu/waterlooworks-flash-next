"""Primitive exception classification, not fabricated HTTP/platform responses."""

import errno
import socket
from http.client import (
    BadStatusLine,
    HTTPException,
    IncompleteRead,
    RemoteDisconnected,
    UnknownProtocol,
)
from ssl import SSLError
from urllib.error import URLError

import pytest

from waterlooworks_flash_next.detail_fetch import transport_failure


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError(),
        ConnectionResetError(),
        ConnectionAbortedError(),
        BrokenPipeError(),
        socket.gaierror(socket.EAI_AGAIN, "temporary"),
        IncompleteRead(b""),
        RemoteDisconnected(""),
        OSError(errno.ECONNRESET, "reset"),
    ],
)
def test_recognized_transient_errors_retry_directly_or_urlwrapped(
    error: Exception,
) -> None:
    assert transport_failure(error).retryable
    assert transport_failure(URLError(error)).retryable


@pytest.mark.parametrize(
    "error",
    [
        socket.gaierror(socket.EAI_NONAME, "permanent"),
        UnknownProtocol("unsupported"),
        BadStatusLine("invalid"),
        HTTPException(),
        ConnectionRefusedError(),
        OSError(errno.EACCES, "denied"),
        SSLError(),
        URLError("unclassified"),
    ],
)
def test_unclassified_permanent_and_protocol_failures_are_terminal(
    error: Exception,
) -> None:
    assert not transport_failure(error).retryable
    assert not transport_failure(URLError(error)).retryable
