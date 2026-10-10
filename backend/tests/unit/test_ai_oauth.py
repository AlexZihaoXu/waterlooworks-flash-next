"""OAuth login ports against in-process fakes of the auth endpoints.

The fakes are httpx.MockTransport handlers: no request leaves the process and
OpenAI's real auth server is never contacted. Tokens are generated test values.
"""

import base64
import hashlib
import json
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet

from waterlooworks_flash_next.ai_oauth import (
    CHATGPT_REDIRECT_URI,
    LoginManager,
    OAuthEndpoints,
    OAuthFlowError,
    codex_account_id,
)
from waterlooworks_flash_next.ai_settings import AISettingsStore
from waterlooworks_flash_next.database import open_database

ENDPOINTS = OAuthEndpoints(
    chatgpt_authorize="https://auth.test.invalid/authorize",
    chatgpt_token="https://auth.test.invalid/oauth/token",
    codex_token="https://auth.test.invalid/codex/token",
    codex_user_code="https://auth.test.invalid/device/usercode",
    codex_device_token="https://auth.test.invalid/device/token",
)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AISettingsStore:
    monkeypatch.setenv("WW_CREDENTIALS_KEY", Fernet.generate_key().decode())
    path = tmp_path / "empty.sqlite3"
    open_database(path).close()
    return AISettingsStore(path)


class FakeAuth:
    """Scripted auth server; records every request it receives."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.token_response: dict[str, object] = {}
        self.device_statuses: list[tuple[int, dict[str, object]]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/oauth/token":
            return httpx.Response(200, json=self.token_response)
        if path == "/device/usercode":
            return httpx.Response(
                200,
                json={
                    "device_auth_id": "dev-1",
                    "user_code": "ABCD-1234",
                    "interval": "0",
                },
            )
        if path == "/device/token":
            status, body = self.device_statuses.pop(0)
            return httpx.Response(status, json=body)
        if path == "/codex/token":
            return httpx.Response(200, json=self.token_response)
        return httpx.Response(404)


def _manager(store: AISettingsStore, fake: FakeAuth, **options: float) -> LoginManager:
    return LoginManager(
        store,
        ENDPOINTS,
        transport=httpx.MockTransport(fake.handler),
        min_interval=0.0,
        **options,
    )


def _chatgpt_tokens(
    scope: str = "openid chatgpt.tokens.use.direct",
) -> dict[str, object]:
    return {
        "access_token": secrets.token_urlsafe(32),
        "refresh_token": secrets.token_urlsafe(32),
        "id_token": secrets.token_urlsafe(16),
        "scope": scope,
        "expires_in": 3600,
    }


def _callback(status_url: str, **override: str) -> str:
    state = parse_qs(urlsplit(status_url).query)["state"][0]
    params = {"code": "code-1", "state": state, "client_id": "issued-client"} | override
    query = "&".join(f"{key}={value}" for key, value in params.items())
    return f"{CHATGPT_REDIRECT_URI}?{query}"


def test_chatgpt_authorize_url_uses_pkce_state_and_stable_device(
    store: AISettingsStore,
) -> None:
    manager = _manager(store, FakeAuth())
    status = manager.start_chatgpt("admin")
    assert status.authorize_url is not None
    query = parse_qs(urlsplit(status.authorize_url).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == [CHATGPT_REDIRECT_URI]
    assert query["resource"] == ["https://api.openai.com/v1"]
    assert "chatgpt.tokens.use.direct" in query["scope"][0].split()
    assert query["ext_agent_host_id"] == [f"urn:uuid:{store.device_id()}"]
    assert len(query["state"][0]) >= 43 and len(query["nonce"][0]) >= 43
    assert status.expires_at > time.time() + 800
    assert manager.status("someone-else") is None


@pytest.mark.parametrize(
    ("mutate", "category"),
    [
        (lambda url: url.replace("127.0.0.1", "localhost"), "redirect_mismatch"),
        (lambda url: url.replace(":1455", ":1456"), "redirect_mismatch"),
        (lambda url: url.replace("/auth/callback", "/other"), "redirect_mismatch"),
        (lambda url: url.replace("http://", "https://"), "redirect_mismatch"),
        (lambda url: url + "#fragment", "redirect_mismatch"),
        (
            lambda url: url.split("state=")[0] + "state=forged&client_id=c",
            "state_mismatch",
        ),
    ],
)
def test_chatgpt_paste_rejects_wrong_origin_path_or_state(
    store: AISettingsStore,
    mutate: Callable[[str], str],
    category: str,
) -> None:
    fake = FakeAuth()
    manager = _manager(store, fake)
    status = manager.start_chatgpt("admin")
    assert status.authorize_url is not None
    with pytest.raises(OAuthFlowError) as error:
        manager.complete_chatgpt("admin", mutate(_callback(status.authorize_url)))
    assert error.value.category == category
    assert fake.requests == []  # nothing was exchanged
    assert store.load_slot("chatgpt_signin") is None


def test_chatgpt_requires_direct_token_scope_and_id_token(
    store: AISettingsStore,
) -> None:
    fake = FakeAuth()
    manager = _manager(store, fake)
    status = manager.start_chatgpt("admin")
    fake.token_response = _chatgpt_tokens(scope="openid profile")
    with pytest.raises(OAuthFlowError) as error:
        manager.complete_chatgpt("admin", _callback(status.authorize_url or ""))
    assert error.value.category == "missing_scope"
    assert store.load_slot("chatgpt_signin") is None
    status = manager.start_chatgpt("admin")
    fake.token_response = _chatgpt_tokens() | {"id_token": ""}
    with pytest.raises(OAuthFlowError) as error:
        manager.complete_chatgpt("admin", _callback(status.authorize_url or ""))
    assert error.value.category == "token_exchange"


def test_chatgpt_success_stores_encrypted_credential_with_issued_client(
    store: AISettingsStore,
) -> None:
    fake = FakeAuth()
    manager = _manager(store, fake)
    status = manager.start_chatgpt("admin")
    fake.token_response = _chatgpt_tokens()
    done = manager.complete_chatgpt("admin", _callback(status.authorize_url or ""))
    assert done.state == "complete" and done.authorize_url is None
    exchange = parse_qs(fake.requests[0].content.decode())
    assert exchange["grant_type"] == ["authorization_code"]
    assert exchange["client_id"] == ["issued-client"]
    assert exchange["resource"] == ["https://api.openai.com/v1"]
    query = parse_qs(urlsplit(status.authorize_url or "").query)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(exchange["code_verifier"][0].encode()).digest()
    ).rstrip(b"=")
    assert query["code_challenge"] == [challenge.decode()]
    stored = store.load_slot("chatgpt_signin")
    assert stored is not None
    assert stored["access"] == fake.token_response["access_token"]
    assert stored["clientId"] == "issued-client"
    assert "chatgpt.tokens.use.direct" in stored["scopes"]
    assert stored["expires"] < time.time() * 1000 + 3600 * 1000
    assert str(fake.token_response["access_token"]) not in done.model_dump_json()
    with pytest.raises(OAuthFlowError):  # one use only
        manager.complete_chatgpt("admin", _callback(status.authorize_url or ""))


def test_chatgpt_login_expires_and_is_bound_to_the_admin(
    store: AISettingsStore,
) -> None:
    manager = _manager(store, FakeAuth(), ttl_seconds=0)
    status = manager.start_chatgpt("admin")
    assert status.state == "expired" and status.authorize_url is None
    assert manager._pending is not None and manager._pending.authorize_url
    url = manager._pending.authorize_url
    with pytest.raises(OAuthFlowError) as error:
        manager.complete_chatgpt("other-admin", _callback(url))
    assert error.value.category == "no_pending_login"
    with pytest.raises(OAuthFlowError) as error:
        manager.complete_chatgpt("admin", _callback(url))
    assert error.value.category == "expired"


def _jwt(account: str) -> str:
    def part(value: dict[str, object]) -> str:
        return (
            base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()
        )

    payload: dict[str, object] = {
        "https://api.openai.com/auth": {"chatgpt_account_id": account}
    }
    return f"{part({'alg': 'none'})}.{part(payload)}.sig"


def _wait(manager: LoginManager, state: str) -> None:
    deadline = time.time() + 5
    while time.time() < deadline:
        status = manager.status("admin")
        if status is not None and status.state == state:
            return
        time.sleep(0.01)
    raise AssertionError("login did not reach the expected state")


def test_codex_device_polls_pending_slow_down_then_stores(
    store: AISettingsStore,
) -> None:
    fake = FakeAuth()
    fake.device_statuses = [
        (403, {}),
        (400, {"error": {"code": "deviceauth_authorization_pending"}}),
        (400, {"error": "slow_down"}),
        (200, {"authorization_code": "auth-code", "code_verifier": "verifier"}),
    ]
    fake.token_response = {
        "access_token": _jwt("acct-123"),
        "refresh_token": secrets.token_urlsafe(32),
        "expires_in": 3600,
    }
    manager = _manager(store, fake)
    status = manager.start_codex("admin")
    assert status.user_code == "ABCD-1234"
    assert status.verification_uri == "https://auth.openai.com/codex/device"
    # slow_down adds 5 seconds; keep the test fast by resetting the interval.
    deadline = time.time() + 5
    while fake.device_statuses and time.time() < deadline:
        if manager._pending is not None and manager._pending.interval > 1:
            manager._pending.interval = 0.0
        time.sleep(0.005)
    _wait(manager, "complete")
    stored = store.load_slot("codex_device")
    assert stored is not None and stored["accountId"] == "acct-123"
    exchange = parse_qs(fake.requests[-1].content.decode())
    assert exchange["redirect_uri"] == ["https://auth.openai.com/deviceauth/callback"]
    assert manager.status("admin").user_code is None  # type: ignore[union-attr]


def test_codex_device_cancel_timeout_and_failure(store: AISettingsStore) -> None:
    fake = FakeAuth()
    fake.device_statuses = [(403, {})] * 10_000
    manager = _manager(store, fake)
    manager.start_codex("admin")
    manager.cancel("admin")
    _wait(manager, "cancelled")
    manager = _manager(store, fake, ttl_seconds=0.05)
    manager.start_codex("admin")
    _wait(manager, "expired")
    failing = FakeAuth()
    failing.device_statuses = [(500, {"error": "server_error"})]
    manager = _manager(store, failing)
    manager.start_codex("admin")
    _wait(manager, "failed")
    assert manager.status("admin").error_category == "device_flow_failed"  # type: ignore[union-attr]
    assert store.load_slot("codex_device") is None


def test_codex_account_id_requires_the_claim() -> None:
    assert codex_account_id(_jwt("acct")) == "acct"
    assert codex_account_id("not-a-jwt") is None
    assert codex_account_id("a.b.c") is None
