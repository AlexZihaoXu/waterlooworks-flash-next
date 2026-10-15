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
from waterlooworks_flash_next.approvals import ApprovalStore

AUTH_HEADERS = {"origin": "https://localhost", "x-ww-request": "1"}
TEST_OWNER = "internal-session-test"


def approve_member(path: Path, uid: str, *, by: str = TEST_OWNER) -> None:
    """Admit a member through the real admission store (the test owner approves).

    New identities start pending; member tests that need access call this.
    """
    ApprovalStore(path).approve(by, uid)


def member_cookie(path: Path, uid: str = "member-uid", *, approved: bool = True) -> str:
    """A real member session; approved through the admission store by default."""
    store = SessionStore(path)
    now = int(time.time())
    cookie = store.issue(store.prepare(None), uid, now + 300, auth_time=now).cookie
    if approved:
        approve_member(path, uid)
    return cookie


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
        store = SessionStore(path, bootstrap=BootstrapOwner(uid=TEST_OWNER))
        cookie = store.prepare(None)
        now = int(time.time())
        cookie = store.issue(cookie, TEST_OWNER, now + 3600, auth_time=now).cookie
        client.cookies.set(COOKIE, cookie, domain="localhost.local", path="/api")
        yield client
