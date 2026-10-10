"""Admin AI settings routes: admin-only, no-store, secret-free, no model calls.

OAuth and custom-server checks use in-process httpx.MockTransport fakes; the
model catalog is a stub. Nothing here contacts OpenAI or any real host.
"""

import secrets
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from auth_support import session_client
from cryptography.fernet import Fernet

from waterlooworks_flash_next.ai_oauth import (
    CHATGPT_REDIRECT_URI,
    LoginManager,
    OAuthEndpoints,
)
from waterlooworks_flash_next.ai_settings import AISettingsStore
from waterlooworks_flash_next.api import create_app
from waterlooworks_flash_next.app_auth import COOKIE, SessionStore

SUPPRESSED = {"detail": "Invalid request; input diagnostics suppressed."}


@pytest.fixture
def path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WW_CREDENTIALS_KEY", Fernet.generate_key().decode())
    return tmp_path / "empty.sqlite3"


def test_settings_save_and_activation_never_call_a_model(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(path)
    calls: list[str] = []

    def no_network(*_args: object, **_kwargs: object) -> object:
        calls.append("network")
        raise AssertionError("saving must not contact a provider")

    from waterlooworks_flash_next import ai_custom, ai_oauth, pi_bridge

    monkeypatch.setattr(ai_custom, "_pinned_get", no_network)
    monkeypatch.setattr(ai_oauth, "_post", no_network)
    monkeypatch.setattr(pi_bridge.PiSession, "start", no_network)
    app.state.ai_catalog = no_network
    secret = secrets.token_urlsafe(24)
    with session_client(path, app=app) as client:
        view = client.get("/api/admin/ai")
        assert view.status_code == 200
        assert view.headers["cache-control"] == "no-store"
        assert view.json()["active"] is None
        draft = {
            "access_mode": "custom",
            "model_id": "ctx384k",
            "reasoning_effort": "medium",
            "parallel_streams": 1,
            "custom_base_url": "http://100.64.0.10:8000/v1",
            "custom_context_window": 393216,
            "custom_reasoning_field": "reasoning_effort",
            "custom_disable_with": {"reasoning_effort": "none"},
        }
        saved = client.put("/api/admin/ai", json=draft)
        assert saved.status_code == 200
        assert saved.json()["draft"]["model_id"] == "ctx384k"
        assert saved.json()["active"] is None
        token = client.put("/api/admin/ai/custom/token", json={"token": secret})
        assert token.status_code == 200 and secret not in token.text
        assert token.json()["credentials"]["custom_token"]["configured"] is True
        assert client.post("/api/admin/ai/activate", json={}).status_code == 422
        active = client.post("/api/admin/ai/activate", json={"confirm": True})
        assert active.status_code == 200
        assert active.json()["active_label"] == "Custom server / ctx384k"
        for public in (client.get("/api/admin/ai"), client.get("/api/workspace")):
            assert secret not in public.text
        for invalid in (
            {**draft, "unexpected": secret},
            {**draft, "parallel_streams": 9},
            {**draft, "model_id": f"bad {secret}"},
        ):
            response = client.put("/api/admin/ai", json=invalid)
            assert response.status_code == 422 and response.json() == SUPPRESSED
        response = client.put(
            "/api/admin/ai/custom/token",
            content='{"token": "' + secret,
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 422 and secret not in response.text
        removed = client.request(
            "DELETE", "/api/admin/ai/credentials/custom_token", json={}
        )
        assert removed.json()["credentials"]["custom_token"]["configured"] is False
        assert (
            client.post("/api/admin/ai/deactivate", json={"confirm": True}).json()[
                "active"
            ]
            is None
        )
    assert calls == []


def test_routes_are_admin_only(path: Path) -> None:
    with session_client(path) as client:
        sessions = SessionStore(path)
        member = sessions.prepare(None)
        now = int(time.time())
        member = sessions.issue(member, "member", now + 300, auth_time=now).cookie
        client.cookies.clear()
        client.cookies.set(COOKIE, member, domain="localhost.local", path="/api")
        assert client.get("/api/admin/ai").status_code == 403
        assert client.get("/api/admin/ai/login").status_code == 403
        for route in (
            "/api/admin/ai/chatgpt/start",
            "/api/admin/ai/codex/start",
            "/api/admin/ai/custom/test",
            "/api/admin/ai/login/cancel",
        ):
            assert client.post(route, json={}).status_code == 403
        assert client.put("/api/admin/ai", json={}).status_code == 403


def test_catalog_and_custom_checks_run_only_on_explicit_actions(path: Path) -> None:
    app = create_app(path)
    app.state.ai_catalog = lambda provider: [
        {
            "id": "gpt-5.6-luna",
            "name": f"Luna via {provider}",
            "context_window": 272000,
            "max_output_tokens": 128000,
            "reasoning": True,
            "efforts": ["off", "low", "medium"],
        }
    ]
    token = secrets.token_urlsafe(24)
    seen: list[httpx.Request] = []

    def server(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "data": [{"id": "ctx384k", "loaded": True, "context_length": 393216}]
            },
        )

    app.state.ai_custom_resolver = lambda _host, _port: ["100.64.0.10"]
    app.state.ai_custom_transport = httpx.MockTransport(server)
    with session_client(path, app=app) as client:
        models = client.get("/api/admin/ai/models?mode=codex_device").json()["models"]
        assert models[0]["name"] == "Luna via openai-codex"
        assert models[0]["efforts"] == ["off", "low", "medium"]
        assert client.get("/api/admin/ai/models?mode=custom").status_code == 422
        assert client.post("/api/admin/ai/custom/test", json={}).status_code == 409
        client.put(
            "/api/admin/ai",
            json={"access_mode": "custom", "custom_base_url": "http://strata.lan/v1"},
        )
        client.put("/api/admin/ai/custom/token", json={"token": token})
        test = client.post("/api/admin/ai/custom/test", json={}).json()
        assert test["reachable"] is True and test["model_count"] == 1
        assert token not in str(test)
        listed = client.post("/api/admin/ai/custom/models", json={}).json()["models"]
        assert listed[0] == {
            "id": "ctx384k",
            "name": "ctx384k",
            "loaded": True,
            "context_window": 393216,
            "supports_tools": None,
            "efforts": ["off"],
            "default_effort": None,
            "reasoning_field": None,
            "disable_with": None,
        }
        assert seen[0].url.host == "100.64.0.10"
        assert seen[0].headers["authorization"] == f"Bearer {token}"
        metadata = "http://169.254.169.254/"
        refused = client.put(
            "/api/admin/ai",
            json={"access_mode": "custom", "custom_base_url": metadata},
        )
        assert refused.status_code == 422
        assert refused.json() == {"detail": "Custom server base URL is not allowed."}
        # A name passes structural checks; resolution to a blocked address is
        # refused when the explicit test runs (and again at activation).
        app.state.ai_custom_resolver = lambda _host, _port: ["169.254.169.254"]
        blocked = client.post("/api/admin/ai/custom/test", json={}).json()
        assert blocked["error_category"] == "blocked_address"
        client.put(
            "/api/admin/ai",
            json={
                "access_mode": "custom",
                "model_id": "ctx384k",
                "custom_base_url": "http://strata.lan/v1",
                "custom_context_window": 393216,
            },
        )
        activation = client.post("/api/admin/ai/activate", json={"confirm": True})
        assert activation.status_code == 409
        assert activation.json() == {"detail": "Custom server address is not allowed."}
        assert len(seen) == 2


def test_chatgpt_paste_flow_through_the_api(path: Path) -> None:
    app = create_app(path)
    tokens = {
        "access_token": secrets.token_urlsafe(32),
        "refresh_token": secrets.token_urlsafe(32),
        "id_token": "id",
        "scope": "openid chatgpt.tokens.use.direct",
        "expires_in": 3600,
    }
    app.state.ai_logins = LoginManager(
        AISettingsStore(path),
        OAuthEndpoints(
            chatgpt_authorize="https://auth.test.invalid/authorize",
            chatgpt_token="https://auth.test.invalid/token",
        ),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json=tokens)
        ),
    )
    with session_client(path, app=app) as client:
        assert client.get("/api/admin/ai/login").json() == {"login": None}
        started = client.post("/api/admin/ai/chatgpt/start", json={}).json()["login"]
        url = started["authorize_url"]
        state = parse_qs(urlsplit(url).query)["state"][0]
        wrong = client.post(
            "/api/admin/ai/chatgpt/complete",
            json={
                "callback_url": f"http://localhost:1455/auth/callback?code=c&state={state}"
            },
        )
        assert wrong.status_code == 400
        assert wrong.json()["detail"].endswith("redirect_mismatch.")
        done = client.post(
            "/api/admin/ai/chatgpt/complete",
            json={
                "callback_url": f"{CHATGPT_REDIRECT_URI}?code=c&state={state}"
                "&client_id=issued"
            },
        )
        assert done.status_code == 200 and done.json()["login"]["state"] == "complete"
        assert tokens["access_token"] not in done.text
        view = client.get("/api/admin/ai").json()
        assert view["credentials"]["chatgpt_signin"]["configured"] is True
        assert str(tokens["refresh_token"]) not in str(view)


def test_missing_credentials_key_is_a_fixed_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WW_CREDENTIALS_KEY", raising=False)
    path = tmp_path / "empty.sqlite3"
    with session_client(path) as client:
        for response in (
            client.get("/api/admin/ai"),
            client.put("/api/admin/ai", json={"access_mode": "custom"}),
        ):
            assert response.status_code == 503
            assert response.json() == {
                "detail": "AI settings unavailable; check the backend key."
            }
            assert response.headers["cache-control"] == "no-store"


def test_catalog_spawns_are_bounded(path: Path) -> None:
    app = create_app(path)
    spawned: list[str] = []

    def catalog(provider: str) -> list[object]:
        spawned.append(provider)
        return []

    app.state.ai_catalog = catalog
    with session_client(path, app=app) as client:
        slots = app.state.ai_catalog_slots
        assert slots.acquire(blocking=False) and slots.acquire(blocking=False)
        busy = client.get("/api/admin/ai/models?mode=openai_api_key")
        assert busy.status_code == 429 and spawned == []
        slots.release()
        assert client.get("/api/admin/ai/models?mode=openai_api_key").json() == {
            "models": []
        }
        assert spawned == ["openai"]
        assert slots.acquire(blocking=False)  # the slot was returned
