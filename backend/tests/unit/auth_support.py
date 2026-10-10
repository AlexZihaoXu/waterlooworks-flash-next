"""Test-only readiness override; real hashed session/role/transport guards remain."""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from waterlooworks_flash_next.api import create_app
from waterlooworks_flash_next.app_auth import (
    COOKIE,
    BootstrapOwner,
    SessionStore,
    require_firebase_ready,
)

AUTH_HEADERS = {"origin": "https://localhost", "x-ww-request": "1"}


def configuration_ready() -> None:
    """Only key configuration is outside empty-database authorization tests."""


@contextmanager
def session_client(path: Path, *, app: FastAPI | None = None) -> Iterator[TestClient]:
    instance = app or create_app(path)
    instance.dependency_overrides[require_firebase_ready] = configuration_ready
    with TestClient(
        instance, base_url="https://localhost", headers=AUTH_HEADERS
    ) as client:
        # Explicit test owner pin; production reads the operator's runtime pin.
        store = SessionStore(
            path, bootstrap=BootstrapOwner(uid="internal-session-test")
        )
        cookie = store.prepare(None)
        store.issue(cookie, "internal-session-test", int(time.time()) + 3600)
        client.cookies.set(COOKIE, cookie, domain="localhost.local", path="/api")
        yield client
