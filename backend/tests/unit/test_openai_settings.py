"""Empty databases/random secrets; freshness uses primitives, not SDK stubs."""

import secrets
import sqlite3
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from waterlooworks_flash_next.app_auth import AuthError, BootstrapOwner, SessionStore
from waterlooworks_flash_next.credentials import CredentialError
from waterlooworks_flash_next.database import SCHEMA_VERSION, open_database
from waterlooworks_flash_next.openai_settings import OpenAIKey, OpenAISettingsStore


def setup_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, SessionStore, str, OpenAISettingsStore]:
    path = tmp_path / "empty.sqlite3"
    open_database(path).close()
    monkeypatch.setenv("WW_CREDENTIALS_KEY", Fernet.generate_key().decode())
    sessions = SessionStore(path, bootstrap=BootstrapOwner(uid="owner"))
    cookie = sessions.prepare(None, now=100)
    cookie = sessions.issue(cookie, "owner", 1000, auth_time=100, now=100).cookie
    store = OpenAISettingsStore(path)
    return path, sessions, cookie, store


def test_maximum_unicode_key_fits_encrypted_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, cookie, store = setup_store(tmp_path, monkeypatch)
    value = SecretStr("\U0001f512" * 512)
    assert store.save(OpenAIKey(api_key=value)).configured
    challenge = store.prepare_reveal(cookie, "owner", now=100.25)
    assert (
        store.consume_reveal(cookie, "owner", challenge.challenge, 101, 1000, now=102)
        == value.get_secret_value()
    )


def test_key_encryption_status_redaction_and_no_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, _, _, store = setup_store(tmp_path, monkeypatch)
    key = OpenAIKey(api_key=SecretStr(secrets.token_urlsafe(48)))
    assert key.model_dump() == {}
    assert key.api_key.get_secret_value() not in repr(key)
    assert not store.status().configured
    assert store.save(key).configured
    assert key.api_key.get_secret_value() not in store.status().model_dump_json()
    with sqlite3.connect(path) as db:
        token = db.execute("SELECT ciphertext FROM openai_credentials").fetchone()[0]
        assert key.api_key.get_secret_value().encode() not in token
    monkeypatch.setenv("WW_CREDENTIALS_KEY", Fernet.generate_key().decode())
    with pytest.raises(CredentialError):
        store.save(key)
    with pytest.raises(CredentialError):
        store.status()


def test_challenge_boundary_one_use_and_session_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, sessions, cookie, store = setup_store(tmp_path, monkeypatch)
    secret = secrets.token_urlsafe(48)
    store.save(OpenAIKey(api_key=SecretStr(secret)))
    challenge = store.prepare_reveal(cookie, "owner", now=100.25)
    assert challenge.not_before == 101
    with pytest.raises(AuthError):
        store.consume_reveal(cookie, "owner", challenge.challenge, 100, 1000, now=101)
    other = sessions.prepare(None, now=100)
    other = sessions.issue(other, "owner", 1000, auth_time=100, now=100).cookie
    with pytest.raises(AuthError):
        store.consume_reveal(other, "owner", challenge.challenge, 101, 1000, now=102)
    assert (
        store.consume_reveal(cookie, "owner", challenge.challenge, 101, 1000, now=102)
        == secret
    )
    with pytest.raises(AuthError):
        store.consume_reveal(cookie, "owner", challenge.challenge, 101, 1000, now=102)


@pytest.mark.parametrize(
    "denial",
    ["logout", "account", "expired", "future", "token-expired", "replaced", "member"],
)
def test_reveal_denials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, denial: str
) -> None:
    _, sessions, cookie, store = setup_store(tmp_path, monkeypatch)
    store.save(OpenAIKey(api_key=SecretStr(secrets.token_urlsafe(48))))
    challenge = store.prepare_reveal(cookie, "owner", now=100.25)
    uid, auth_time, expiry, now = "owner", 101, 1000, 102
    if denial == "logout":
        sessions.logout(cookie)
    elif denial == "account":
        uid = "other"
    elif denial == "expired":
        now = 401
    elif denial == "future":
        auth_time = 103
    elif denial == "token-expired":
        expiry = 102
    elif denial == "replaced":
        store.prepare_reveal(cookie, "owner", now=101)
    elif denial == "member":
        cookie = sessions.prepare(None, now=100)
        cookie = sessions.issue(cookie, "member", 1000, auth_time=100, now=100).cookie
        uid = "member"
    with pytest.raises(AuthError):
        store.consume_reveal(
            cookie, uid, challenge.challenge, auth_time, expiry, now=now
        )


def test_prepare_checks_admin_and_bounds_challenges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, sessions, cookie, store = setup_store(tmp_path, monkeypatch)
    for now in (100.25, 101, 102):
        store.prepare_reveal(cookie, "owner", now=now)
    with sqlite3.connect(path) as db:
        assert (
            db.execute("SELECT count(*) FROM openai_reauth_challenges").fetchone()[0]
            == 1
        )
    member = sessions.prepare(None, now=100)
    member = sessions.issue(member, "member", 1000, auth_time=100, now=100).cookie
    with pytest.raises(AuthError):
        store.prepare_reveal(member, "member", now=102)
    sessions.logout(cookie)
    with pytest.raises(AuthError):
        store.validate_challenge(cookie, "owner", "invalid", now=102)
    with sqlite3.connect(path) as db:
        assert (
            db.execute("SELECT count(*) FROM openai_reauth_challenges").fetchone()[0]
            == 0
        )


def test_settings_api_admin_csrf_redaction_without_provider_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    from auth_support import session_client

    from waterlooworks_flash_next.app_auth import COOKIE

    path = tmp_path / "empty.sqlite3"
    monkeypatch.setenv("WW_CREDENTIALS_KEY", Fernet.generate_key().decode())
    endpoint = "/api/admin/openai-key"
    secret = secrets.token_urlsafe(48)
    with session_client(path) as client:
        assert client.get(endpoint).json() == {
            "configured": False,
            "status": "not_configured",
            "updated_at": None,
        }
        response = client.post(endpoint, json={"api_key": secret})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        for public in (
            response,
            client.get(endpoint),
            client.get("/api/workspace"),
            client.get("/api/refresh/events?once=true"),
        ):
            assert secret not in public.text
        prepared = client.post(endpoint + "/reveal/prepare", json={})
        assert prepared.status_code == 200
        assert secret not in prepared.text
        assert prepared.json()["not_before"] <= time.time()
        assert prepared.headers["cache-control"] == "no-store"
        for route, body in (
            (endpoint, {"api_key": secret}),
            (endpoint + "/reveal/prepare", {}),
            (
                endpoint + "/reveal",
                {"challenge": secrets.token_urlsafe(32), "id_token": secret},
            ),
        ):
            for headers in (
                {"origin": ""},
                {"origin": "https://evil.invalid"},
                {"x-ww-request": ""},
            ):
                assert client.post(route, json=body, headers=headers).status_code == 403
            assert client.post(route, content="").status_code == 415
            for invalid in ({**body, secret: secret}, {**body, "api_key": [secret]}):
                response = client.post(route, json=invalid)
                assert response.status_code == 422
                assert response.json() == {
                    "detail": "Invalid request; input diagnostics suppressed."
                }
            response = client.post(
                route,
                content='{"' + secret,
                headers={"content-type": "application/json"},
            )
            assert response.status_code == 422 and secret not in response.text
        # Nonexistent nonce denies before touching Firebase verification at all.
        response = client.post(
            endpoint + "/reveal",
            json={"challenge": secrets.token_urlsafe(32), "id_token": secret},
        )
        assert response.status_code == 401 and secret not in response.text
        sessions = SessionStore(path)
        member = sessions.prepare(None)
        now = int(time.time())
        member = sessions.issue(member, "member", now + 300, auth_time=now).cookie
        client.cookies.clear()
        client.cookies.set(COOKIE, member, domain="localhost.local", path="/api")
        assert client.get(endpoint).status_code == 403
        for suffix, body in (
            ("", {"api_key": secret}),
            ("/reveal/prepare", {}),
            ("/reveal", {"challenge": secrets.token_urlsafe(32), "id_token": secret}),
        ):
            assert client.post(endpoint + suffix, json=body).status_code == 403
        sessions.logout(member)
        assert client.get(endpoint).status_code == 401
        assert client.post(endpoint + "/reveal/prepare", json={}).status_code == 401


def test_challenge_cap_and_expired_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, _, cookie, store = setup_store(tmp_path, monkeypatch)
    hashes = [secrets.token_hex(32) for _ in range(1000)]
    with sqlite3.connect(path) as db:
        db.executemany(
            "INSERT INTO app_sessions VALUES (?, 'owner', 1000)",
            ((value,) for value in hashes),
        )
        db.executemany(
            "INSERT INTO openai_reauth_challenges "
            "VALUES (?, ?, 'owner', 100.25, 101, 400)",
            ((secrets.token_hex(32), value) for value in hashes),
        )
    with pytest.raises(AuthError):
        store.prepare_reveal(cookie, "owner", now=102)
    store.prepare_reveal(cookie, "owner", now=401)
    with sqlite3.connect(path) as db:
        assert (
            db.execute("SELECT count(*) FROM openai_reauth_challenges").fetchone()[0]
            == 1
        )


def test_tampered_reveal_fails_closed_and_consumes_challenge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, _, cookie, store = setup_store(tmp_path, monkeypatch)
    key = OpenAIKey(api_key=SecretStr(secrets.token_urlsafe(48)))
    store.save(key)
    challenge = store.prepare_reveal(cookie, "owner", now=100.25)
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE openai_credentials SET ciphertext=?", (secrets.token_bytes(100),)
        )
    with pytest.raises(CredentialError):
        store.consume_reveal(cookie, "owner", challenge.challenge, 101, 1000, now=102)
    with pytest.raises(AuthError):
        store.consume_reveal(cookie, "owner", challenge.challenge, 101, 1000, now=102)
    with pytest.raises(CredentialError):
        store.save(key)


def test_concurrent_reveal_only_decrypts_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    _, _, cookie, store = setup_store(tmp_path, monkeypatch)
    secret = secrets.token_urlsafe(48)
    store.save(OpenAIKey(api_key=SecretStr(secret)))
    challenge = store.prepare_reveal(cookie, "owner", now=100.25)

    def consume() -> bool:
        try:
            return (
                store.consume_reveal(
                    cookie, "owner", challenge.challenge, 101, 1000, now=102
                )
                == secret
            )
        except AuthError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _: consume(), range(2))) == [False, True]


def test_schema13_preserves_schema12_pair_and_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from waterlooworks_flash_next import database
    from waterlooworks_flash_next.credentials import CredentialPair, CredentialStore

    with monkeypatch.context() as legacy:
        legacy.setattr(database, "SCHEMA_VERSION", 13)
        path, sessions, cookie, _ = setup_store(tmp_path, monkeypatch)
    store = CredentialStore(path)
    pair = CredentialPair(
        username=SecretStr(secrets.token_urlsafe()),
        password=SecretStr(secrets.token_urlsafe()),
    )
    store.save(pair)
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE openai_reauth_challenges")
        db.execute("DROP TABLE openai_credentials")
        db.execute("PRAGMA user_version=12")
        schema = set(db.execute("SELECT type,name,sql FROM sqlite_master"))
        credentials = db.execute("SELECT * FROM waterlooworks_credentials").fetchall()
        auth = db.execute("SELECT * FROM app_sessions").fetchall()
    db = open_database(path)
    schema = {row for row in schema if row[1] != "inference_jobs"}
    assert schema <= {
        tuple(row) for row in db.execute("SELECT type,name,sql FROM sqlite_master")
    }
    assert credentials == [
        tuple(row) for row in db.execute("SELECT * FROM waterlooworks_credentials")
    ]
    assert auth == [tuple(row) for row in db.execute("SELECT * FROM app_sessions")]
    assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not db.execute("PRAGMA foreign_key_check").fetchall()
    db.close()
    identity = sessions.lookup(cookie, now=102)
    assert identity is not None
    assert identity.role == "admin"
    assert store.load_for_login() == pair


def test_waterlooworks_ciphertext_cannot_be_substituted_for_openai_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from waterlooworks_flash_next.credentials import CredentialPair, CredentialStore

    path, _, cookie, store = setup_store(tmp_path, monkeypatch)
    ww = CredentialPair(
        username=SecretStr(secrets.token_urlsafe()),
        password=SecretStr(secrets.token_urlsafe()),
    )
    CredentialStore(path).save(ww)
    key = OpenAIKey(api_key=SecretStr(secrets.token_urlsafe(48)))
    store.save(key)
    challenge = store.prepare_reveal(cookie, "owner", now=100.25)
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE openai_credentials SET ciphertext="
            "(SELECT ciphertext FROM waterlooworks_credentials)"
        )
    with pytest.raises(CredentialError):
        store.consume_reveal(cookie, "owner", challenge.challenge, 101, 1000, now=102)
    with pytest.raises(CredentialError):
        store.status()
    with pytest.raises(CredentialError):
        store.save(key)


def test_renewal_rebinds_open_reveal_challenge_to_rotated_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, sessions, cookie, store = setup_store(tmp_path, monkeypatch)
    secret = secrets.token_urlsafe(48)
    store.save(OpenAIKey(api_key=SecretStr(secret)))
    challenge = store.prepare_reveal(cookie, "owner", now=100.25)
    # The hourly renewal rotates the cookie while the re-auth popup is open.
    renewed = sessions.issue(cookie, "owner", 1000, auth_time=100, now=101).cookie
    assert renewed != cookie
    # The rotated-from value cannot use the challenge once it moved...
    with pytest.raises(AuthError):
        store.consume_reveal(cookie, "owner", challenge.challenge, 101, 1000, now=102)
    # ...and purging the old row does not cascade the challenge away.
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("DELETE FROM app_sessions WHERE expires_at<=131")
    assert (
        store.consume_reveal(renewed, "owner", challenge.challenge, 101, 1000, now=102)
        == secret
    )
    # A separate fresh login never inherits another session's challenge.
    second = store.prepare_reveal(renewed, "owner", now=102.5)
    other = sessions.prepare(None, now=103)
    other = sessions.issue(other, "owner", 1000, auth_time=103, now=103).cookie
    with pytest.raises(AuthError):
        store.consume_reveal(other, "owner", second.challenge, 104, 1000, now=104)
