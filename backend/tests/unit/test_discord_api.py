"""Per-user Discord webhook routes under the real session/CSRF middleware.

Webhook URLs are generated with random tokens; delivery goes through an
injected ``httpx.MockTransport`` so nothing contacts Discord.
"""

import json
import secrets
import time
from pathlib import Path

import httpx
import pytest
from auth_support import approve_member, session_client
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from waterlooworks_flash_next.app_auth import COOKIE, SessionStore
from waterlooworks_flash_next.discord_alerts import TEST_TITLE

ROUTE = "/api/account/discord-webhooks"
SUPPRESSED = {"detail": "Invalid request; input diagnostics suppressed."}


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WW_CREDENTIALS_KEY", Fernet.generate_key().decode())


def webhook_url(webhook_id: str = "1234567890") -> str:
    return f"https://discord.com/api/webhooks/{webhook_id}/{secrets.token_urlsafe(48)}"


def _member(client: TestClient, path: Path, uid: str = "member-uid") -> None:
    store = SessionStore(path)
    now = int(time.time())
    cookie = store.issue(store.prepare(None), uid, now + 300, auth_time=now).cookie
    approve_member(path, uid)
    client.cookies.clear()
    client.cookies.set(COOKIE, cookie, domain="localhost.local", path="/api")


def test_members_add_list_update_and_delete_their_webhooks(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    with session_client(path) as client:
        _member(client, path)
        empty = client.get(ROUTE)
        assert empty.status_code == 200
        assert empty.headers["cache-control"] == "no-store"
        assert empty.json() == {"webhooks": [], "limit": 10, "available": True}
        url = webhook_url("98765")
        added = client.post(ROUTE, json={"label": "Jobs", "url": url})
        assert added.status_code == 201
        assert added.headers["cache-control"] == "no-store"
        body = added.json()
        assert set(body) == {
            "id",
            "label",
            "hint",
            "enabled",
            "created_at",
            "last_sent_at",
            "last_status",
            "last_error_at",
        }
        assert (body["label"], body["hint"], body["enabled"]) == ("Jobs", "…8765", True)
        listed = client.get(ROUTE)
        assert url not in listed.text and url.rsplit("/", 1)[1] not in listed.text
        assert [item["id"] for item in listed.json()["webhooks"]] == [body["id"]]
        patched = client.patch(f"{ROUTE}/{body['id']}", json={"enabled": False})
        assert patched.status_code == 200
        assert patched.json()["enabled"] is False
        renamed = client.patch(f"{ROUTE}/{body['id']}", json={"label": "Renamed"})
        assert renamed.json()["label"] == "Renamed"
        assert renamed.json()["enabled"] is False
        deleted = client.request("DELETE", f"{ROUTE}/{body['id']}", json={})
        assert deleted.status_code == 200
        assert deleted.json()["webhooks"] == []
        gone = client.request("DELETE", f"{ROUTE}/{body['id']}", json={})
        assert gone.status_code == 404


def test_webhooks_are_private_to_their_owner(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    with session_client(path) as client:
        added = client.post(ROUTE, json={"label": "Admin", "url": webhook_url()})
        webhook_id = added.json()["id"]
        _member(client, path)
        assert client.get(ROUTE).json()["webhooks"] == []
        assert (
            client.patch(f"{ROUTE}/{webhook_id}", json={"enabled": False}).status_code
            == 404
        )
        assert client.post(f"{ROUTE}/{webhook_id}/test", json={}).status_code == 404
        assert (
            client.request("DELETE", f"{ROUTE}/{webhook_id}", json={}).status_code
            == 404
        )


def test_invalid_input_never_echoes_the_url(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    with session_client(path) as client:
        marker = secrets.token_urlsafe(24)
        for body in (
            {"label": "Jobs", "url": f"https://example.com/api/webhooks/1/{marker}"},
            {"label": "Jobs", "url": f"https://discord.com/api/webhooks/1/{marker}?x"},
            {"label": "Jobs", "url": marker},
        ):
            response = client.post(ROUTE, json=body)
            assert response.status_code == 422
            assert marker not in response.text
            assert "Discord webhook URL" in response.json()["detail"]
        for labelled in (
            {"label": "", "url": webhook_url()},
            {"label": "x" * 61, "url": webhook_url()},
        ):
            response = client.post(ROUTE, json=labelled)
            assert response.status_code == 422
            assert labelled["url"] not in response.text
        for shape in (
            {"label": "Jobs"},
            {"url": f"https://discord.com/api/webhooks/1/{marker}"},
            {
                "label": "Jobs",
                "url": f"https://discord.com/api/webhooks/1/{marker}",
                "extra": marker,
            },
            {"label": 1, "url": f"https://discord.com/api/webhooks/1/{marker}"},
        ):
            response = client.post(ROUTE, json=shape)
            assert response.status_code == 422
            assert response.json() == SUPPRESSED
        malformed = client.post(
            ROUTE,
            content=f'{{"label":"x","url":"{marker}"',
            headers={"content-type": "application/json"},
        )
        assert malformed.status_code == 422 and marker not in malformed.text
        added = client.post(ROUTE, json={"label": "Jobs", "url": webhook_url()})
        webhook_id = added.json()["id"]
        for body in ({}, {"enabled": "yes"}, {"label": "x", "extra": marker}):
            response = client.patch(f"{ROUTE}/{webhook_id}", json=body)
            assert response.status_code == 422
            assert marker not in response.text
        for bad in ("abc", "0", "-1"):
            assert client.patch(
                f"{ROUTE}/{bad}", json={"enabled": True}
            ).status_code == (422)
        assert client.get(ROUTE).json()["webhooks"][0]["label"] == "Jobs"


def test_limit_duplicate_and_missing_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "db.sqlite3"
    with session_client(path) as client:
        url = webhook_url("1")
        assert client.post(ROUTE, json={"label": "A", "url": url}).status_code == 201
        duplicate = client.post(ROUTE, json={"label": "B", "url": url})
        assert duplicate.status_code == 409 and url not in duplicate.text
        for index in range(9):
            client.post(ROUTE, json={"label": "X", "url": webhook_url(str(10 + index))})
        full = client.post(ROUTE, json={"label": "Y", "url": webhook_url("99")})
        assert full.status_code == 409
        assert "10" in full.json()["detail"]
        monkeypatch.delenv("WW_CREDENTIALS_KEY")
        assert client.get(ROUTE).json()["available"] is False
        _member(client, path)
        missing = client.post(ROUTE, json={"label": "Z", "url": webhook_url("5")})
        assert missing.status_code == 503
        assert "encryption key" in missing.json()["detail"]
        assert client.get(ROUTE).json()["webhooks"] == []


def test_test_send_uses_the_fixed_message_and_is_rate_limited(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    with session_client(path) as client:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(204)

        alerts = client.app.state.discord_alerts  # type: ignore[attr-defined]
        alerts.transport = httpx.MockTransport(handler)
        url = webhook_url()
        webhook_id = client.post(ROUTE, json={"label": "A", "url": url}).json()["id"]
        sent = client.post(f"{ROUTE}/{webhook_id}/test", json={})
        assert sent.status_code == 200
        assert sent.headers["cache-control"] == "no-store"
        assert sent.json()["last_status"] == "ok"
        assert str(requests[0].url) == url
        payload = json.loads(requests[0].content)
        assert payload["embeds"][0]["title"] == TEST_TITLE
        assert payload["allowed_mentions"] == {"parse": []}
        codes = [
            client.post(f"{ROUTE}/{webhook_id}/test", json={}).status_code
            for _ in range(3)
        ]
        assert codes == [200, 200, 429]
        assert len(requests) == 3
        _member(client, path)
        other = client.post(ROUTE, json={"label": "B", "url": webhook_url("7")})
        assert (
            client.post(f"{ROUTE}/{other.json()['id']}/test", json={}).status_code
            == 200
        )


def test_routes_require_a_session_and_explicit_application_requests(
    tmp_path: Path,
) -> None:
    path = tmp_path / "db.sqlite3"
    with session_client(path) as client:
        for method, url, body in (
            ("POST", ROUTE, {"label": "A", "url": webhook_url()}),
            ("PATCH", f"{ROUTE}/1", {"enabled": True}),
            ("DELETE", f"{ROUTE}/1", {}),
            ("POST", f"{ROUTE}/1/test", {}),
        ):
            assert (
                client.request(
                    method, url, json=body, headers={"x-ww-request": "0"}
                ).status_code
                == 403
            )
            assert (
                client.request(
                    method, url, json=body, headers={"origin": ""}
                ).status_code
                == 403
            )
        client.cookies.clear()
        for method, url in (
            ("GET", ROUTE),
            ("POST", ROUTE),
            ("PATCH", f"{ROUTE}/1"),
            ("DELETE", f"{ROUTE}/1"),
            ("POST", f"{ROUTE}/1/test"),
        ):
            response = client.request(method, url, json={})
            assert response.status_code == 401, (method, url)


def test_agent_api_has_no_discord_routes(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    with session_client(path) as client:
        schema = client.app.openapi()  # type: ignore[attr-defined]
        for route in schema["paths"]:
            if "discord" in route:
                assert route.startswith(ROUTE)


def test_public_base_url_comes_only_from_the_server_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from waterlooworks_flash_next.api import create_app

    monkeypatch.setenv("WW_DATABASE", str(tmp_path / "db.sqlite3"))
    monkeypatch.delenv("WW_PUBLIC_BASE_URL", raising=False)
    assert create_app().state.discord_alerts.base_url is None
    monkeypatch.setenv("WW_PUBLIC_BASE_URL", "https://flash.example/")
    assert create_app().state.discord_alerts.base_url == "https://flash.example"
    # Explicit test databases never inherit it.
    assert create_app(tmp_path / "x.sqlite3").state.discord_alerts.base_url is None
    monkeypatch.setenv("WW_PUBLIC_BASE_URL", "https://flash.example/path")
    with pytest.raises(ValueError):
        create_app()
