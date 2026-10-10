"""Server-side ports of pi-ai's ChatGPT and Codex OAuth logins.

The hosted backend cannot receive the 127.0.0.1:1455 callback, so Sign in with
ChatGPT uses the manual-paste variant: the admin opens the authorize URL, the
browser lands on the unreachable loopback callback, and the admin pastes that
full URL back. Codex uses the device-code flow (unofficial; may break). Login
state lives only in this process, bound to the admin UID, one at a time, with
a 15-minute TTL. Tokens are stored encrypted and never returned. Errors are
fixed categories; provider bodies are never echoed or logged.
"""

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from pydantic import BaseModel

from waterlooworks_flash_next.ai_settings import AISettingsStore, OAuthCredential

LOGIN_TTL_SECONDS = 15 * 60
MAX_RESPONSE_BYTES = 256 * 1024
REQUEST_TIMEOUT = 20.0

# Sign in with ChatGPT (OpenAI provider OAuth; tokens call api.openai.com).
CHATGPT_DYNAMIC_CLIENT_ID = "dynamic_agent_client"
CHATGPT_AGENT_NAME = "WaterlooWorks Flash"
CHATGPT_REDIRECT_URI = "http://127.0.0.1:1455/auth/callback"
CHATGPT_RESOURCE = "https://api.openai.com/v1"
CHATGPT_DIRECT_SCOPE = "chatgpt.tokens.use.direct"
CHATGPT_SCOPE = (
    f"openid profile email offline_access resource.invoke {CHATGPT_DIRECT_SCOPE}"
)
CHATGPT_EXPIRY_MARGIN_MS = 3 * 60 * 1000

# Codex device login (unofficial).
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_VERIFICATION_URI = "https://auth.openai.com/codex/device"
CODEX_DEVICE_REDIRECT_URI = "https://auth.openai.com/deviceauth/callback"
CODEX_JWT_CLAIM = "https://api.openai.com/auth"


@dataclass(frozen=True)
class OAuthEndpoints:
    chatgpt_authorize: str = "https://auth.openai.com/api/accounts/authorize"
    chatgpt_token: str = "https://auth.openai.com/api/accounts/oauth/token"
    codex_token: str = "https://auth.openai.com/oauth/token"
    codex_user_code: str = "https://auth.openai.com/api/accounts/deviceauth/usercode"
    codex_device_token: str = "https://auth.openai.com/api/accounts/deviceauth/token"


DEFAULT_ENDPOINTS = OAuthEndpoints()
LoginKind = Literal["chatgpt_signin", "codex_device"]
LoginState = Literal["pending", "complete", "failed", "expired", "cancelled"]
ErrorCategory = Literal[
    "no_pending_login",
    "expired",
    "redirect_mismatch",
    "state_mismatch",
    "authorization_denied",
    "missing_code",
    "missing_scope",
    "token_exchange",
    "device_code_unavailable",
    "device_flow_failed",
    "storage",
]


class OAuthFlowError(Exception):
    def __init__(self, category: ErrorCategory) -> None:
        super().__init__(category)
        self.category = category


class LoginStatus(BaseModel):
    kind: LoginKind
    state: LoginState
    expires_at: int
    authorize_url: str | None = None
    user_code: str | None = None
    verification_uri: str | None = None
    interval_seconds: int | None = None
    error_category: ErrorCategory | None = None


@dataclass
class _Pending:
    kind: LoginKind
    uid: str
    expires_at: float
    state: LoginState = "pending"
    error: ErrorCategory | None = None
    # ChatGPT
    oauth_state: str = field(default="", repr=False)
    verifier: str = field(default="", repr=False)
    authorize_url: str | None = None
    # Codex
    device_auth_id: str = field(default="", repr=False)
    user_code: str | None = None
    interval: float = 5.0
    cancel: threading.Event = field(default_factory=threading.Event)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _pkce() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _post(
    url: str,
    *,
    form: dict[str, str] | None = None,
    payload: dict[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> tuple[int, dict[str, Any] | None]:
    """Bounded POST to a fixed OpenAI auth endpoint; returns status and JSON."""
    with httpx.Client(
        trust_env=False,
        follow_redirects=False,
        timeout=REQUEST_TIMEOUT,
        transport=transport,
    ) as client:
        with client.stream(
            "POST",
            url,
            data=form,
            json=payload,
            headers={"Accept": "application/json", "Accept-Encoding": "identity"},
        ) as response:
            body = bytearray()
            for chunk in response.iter_bytes(65536):
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise OAuthFlowError("token_exchange")
            try:
                parsed = json.loads(body) if body else None
            except (ValueError, UnicodeError):
                parsed = None
            return response.status_code, parsed if isinstance(parsed, dict) else None


def _token_string(token: dict[str, Any], name: str) -> str:
    value = token.get(name)
    if not isinstance(value, str) or not value.strip() or len(value) > 16_384:
        raise OAuthFlowError("token_exchange")
    return value


def _expires_in(token: dict[str, Any]) -> float:
    value = token.get("expires_in")
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not 0 < value < 10 * 365 * 86400
    ):
        raise OAuthFlowError("token_exchange")
    return float(value)


def codex_account_id(access_token: str) -> str | None:
    parts = access_token.split(".")
    if len(parts) != 3:
        return None
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
        account = claims[CODEX_JWT_CLAIM]["chatgpt_account_id"]
    except (ValueError, KeyError, TypeError):
        return None
    return account if isinstance(account, str) and 0 < len(account) <= 512 else None


class LoginManager:
    """One pending login per process, bound to the admin UID that started it."""

    def __init__(
        self,
        store: AISettingsStore,
        endpoints: OAuthEndpoints = DEFAULT_ENDPOINTS,
        *,
        transport: httpx.BaseTransport | None = None,
        min_interval: float = 1.0,
        ttl_seconds: float = LOGIN_TTL_SECONDS,
    ) -> None:
        self.store = store
        self.endpoints = endpoints
        self.transport = transport
        self.min_interval = min_interval
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._pending: _Pending | None = None
        self._thread: threading.Thread | None = None

    # ----- shared --------------------------------------------------------------

    def _replace(self, pending: _Pending) -> None:
        with self._lock:
            if self._pending is not None:
                self._pending.cancel.set()
            self._pending = pending

    def _current(self, uid: str, kind: LoginKind | None = None) -> _Pending:
        with self._lock:
            pending = self._pending
        if pending is None or pending.uid != uid or (kind and pending.kind != kind):
            raise OAuthFlowError("no_pending_login")
        if pending.state == "pending" and time.time() >= pending.expires_at:
            pending.state, pending.error = "expired", "expired"
            pending.cancel.set()
        return pending

    def status(self, uid: str) -> LoginStatus | None:
        try:
            pending = self._current(uid)
        except OAuthFlowError:
            return None
        return LoginStatus(
            kind=pending.kind,
            state=pending.state,
            expires_at=int(pending.expires_at),
            authorize_url=pending.authorize_url if pending.state == "pending" else None,
            user_code=pending.user_code if pending.state == "pending" else None,
            verification_uri=CODEX_VERIFICATION_URI
            if pending.kind == "codex_device" and pending.state == "pending"
            else None,
            interval_seconds=int(pending.interval)
            if pending.kind == "codex_device"
            else None,
            error_category=pending.error,
        )

    def cancel(self, uid: str) -> None:
        try:
            pending = self._current(uid)
        except OAuthFlowError:
            return
        if pending.state == "pending":
            pending.state = "cancelled"
        pending.cancel.set()

    def close(self) -> None:
        with self._lock:
            if self._pending is not None:
                self._pending.cancel.set()

    # ----- Sign in with ChatGPT (manual paste) ---------------------------------

    def start_chatgpt(self, uid: str) -> LoginStatus:
        device = self.store.device_id()
        verifier, challenge = _pkce()
        state = _b64url(secrets.token_bytes(32))
        nonce = _b64url(secrets.token_bytes(32))
        url = (
            self.endpoints.chatgpt_authorize
            + "?"
            + urlencode(
                {
                    "client_id": CHATGPT_DYNAMIC_CLIENT_ID,
                    "agent_name_hint": CHATGPT_AGENT_NAME,
                    "ext_agent_host_id": f"urn:uuid:{device.lower()}",
                    "response_type": "code",
                    "redirect_uri": CHATGPT_REDIRECT_URI,
                    "resource": CHATGPT_RESOURCE,
                    "scope": CHATGPT_SCOPE,
                    "state": state,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "nonce": nonce,
                }
            )
        )
        self._replace(
            _Pending(
                kind="chatgpt_signin",
                uid=uid,
                expires_at=time.time() + self.ttl_seconds,
                oauth_state=state,
                verifier=verifier,
                authorize_url=url,
            )
        )
        status = self.status(uid)
        assert status is not None
        return status

    def complete_chatgpt(self, uid: str, callback_url: str) -> LoginStatus:
        pending = self._current(uid, "chatgpt_signin")
        if pending.state != "pending":
            raise OAuthFlowError(pending.error or "no_pending_login")
        try:
            parts = urlsplit(callback_url.strip())
            port = parts.port
        except ValueError:
            raise OAuthFlowError("redirect_mismatch") from None
        expected = urlsplit(CHATGPT_REDIRECT_URI)
        if (
            parts.scheme != expected.scheme
            or parts.hostname != expected.hostname
            or port != expected.port
            or parts.path != expected.path
            or parts.username is not None
            or parts.password is not None
            or parts.fragment
        ):
            raise OAuthFlowError("redirect_mismatch")
        query = parse_qs(parts.query, keep_blank_values=True)
        if any(len(values) != 1 for values in query.values()):
            raise OAuthFlowError("redirect_mismatch")
        params = {key: values[0] for key, values in query.items()}
        if "error" in params:
            self._fail(pending, "authorization_denied")
        state = params.get("state", "")
        if not state or not hmac.compare_digest(state, pending.oauth_state):
            raise OAuthFlowError("state_mismatch")
        code, client_id = params.get("code", ""), params.get("client_id", "").strip()
        if not code or not client_id or len(code) > 4096 or len(client_id) > 512:
            raise OAuthFlowError("missing_code")
        try:
            status, token = _post(
                self.endpoints.chatgpt_token,
                form={
                    "grant_type": "authorization_code",
                    "client_id": client_id,
                    "code": code,
                    "code_verifier": pending.verifier,
                    "redirect_uri": CHATGPT_REDIRECT_URI,
                    "resource": CHATGPT_RESOURCE,
                },
                transport=self.transport,
            )
        except OAuthFlowError:
            self._fail(pending, "token_exchange")
        except Exception:
            self._fail(pending, "token_exchange")
        if status != 200 or token is None:
            self._fail(pending, "token_exchange")
        assert token is not None
        try:
            access = _token_string(token, "access_token")
            refresh = _token_string(token, "refresh_token")
            _token_string(token, "id_token")
            scope = _token_string(token, "scope")
            expires_in = _expires_in(token)
        except OAuthFlowError:
            self._fail(pending, "token_exchange")
        scopes = scope.split()
        if CHATGPT_DIRECT_SCOPE not in scopes:
            self._fail(pending, "missing_scope")
        credential = OAuthCredential(
            type="oauth",
            access=access,
            refresh=refresh,
            expires=int(time.time() * 1000 + expires_in * 1000)
            - CHATGPT_EXPIRY_MARGIN_MS,
            clientId=client_id,
            scopes=scopes[:32],
        )
        self._save(pending, "chatgpt_signin", credential)
        result = self.status(uid)
        assert result is not None
        return result

    # ----- Codex device code ---------------------------------------------------

    def start_codex(self, uid: str) -> LoginStatus:
        try:
            status, payload = _post(
                self.endpoints.codex_user_code,
                payload={"client_id": CODEX_CLIENT_ID},
                transport=self.transport,
            )
        except Exception:
            raise OAuthFlowError("device_code_unavailable") from None
        if status != 200 or payload is None:
            raise OAuthFlowError("device_code_unavailable")
        device_auth_id = payload.get("device_auth_id")
        user_code = payload.get("user_code")
        interval = payload.get("interval")
        if isinstance(interval, str) and interval.strip().isdigit():
            interval = int(interval.strip())
        if (
            not isinstance(device_auth_id, str)
            or not 0 < len(device_auth_id) <= 1024
            or not isinstance(user_code, str)
            or not 0 < len(user_code) <= 64
            or isinstance(interval, bool)
            or not isinstance(interval, int | float)
            or not 0 <= interval <= 300
        ):
            raise OAuthFlowError("device_code_unavailable")
        pending = _Pending(
            kind="codex_device",
            uid=uid,
            expires_at=time.time() + self.ttl_seconds,
            device_auth_id=device_auth_id,
            user_code=user_code,
            interval=max(self.min_interval, float(interval)),
        )
        self._replace(pending)
        thread = threading.Thread(
            target=self._poll_codex, args=(pending,), name="codex-device", daemon=True
        )
        self._thread = thread
        thread.start()
        result = self.status(uid)
        assert result is not None
        return result

    def _poll_codex(self, pending: _Pending) -> None:
        try:
            while not pending.cancel.wait(pending.interval):
                if pending.state != "pending":
                    return
                if time.time() >= pending.expires_at:
                    pending.state, pending.error = "expired", "expired"
                    return
                try:
                    status, payload = _post(
                        self.endpoints.codex_device_token,
                        payload={
                            "device_auth_id": pending.device_auth_id,
                            "user_code": pending.user_code or "",
                        },
                        transport=self.transport,
                    )
                except Exception:
                    pending.state, pending.error = "failed", "device_flow_failed"
                    return
                if status == 200 and payload is not None:
                    code = payload.get("authorization_code")
                    verifier = payload.get("code_verifier")
                    if not isinstance(code, str) or not isinstance(verifier, str):
                        pending.state, pending.error = "failed", "device_flow_failed"
                        return
                    self._exchange_codex(pending, code, verifier)
                    return
                if status in (403, 404):
                    continue
                error = (payload or {}).get("error")
                code = error.get("code") if isinstance(error, dict) else error
                if code == "deviceauth_authorization_pending":
                    continue
                if code == "slow_down":
                    pending.interval += 5
                    continue
                pending.state, pending.error = "failed", "device_flow_failed"
                return
        except Exception:
            pending.state, pending.error = "failed", "device_flow_failed"

    def _exchange_codex(self, pending: _Pending, code: str, verifier: str) -> None:
        try:
            status, token = _post(
                self.endpoints.codex_token,
                form={
                    "grant_type": "authorization_code",
                    "client_id": CODEX_CLIENT_ID,
                    "code": code,
                    "code_verifier": verifier,
                    "redirect_uri": CODEX_DEVICE_REDIRECT_URI,
                },
                transport=self.transport,
            )
            if status != 200 or token is None:
                raise OAuthFlowError("token_exchange")
            access = _token_string(token, "access_token")
            refresh = _token_string(token, "refresh_token")
            expires_in = _expires_in(token)
            account = codex_account_id(access)
            if account is None:
                raise OAuthFlowError("token_exchange")
        except Exception:
            pending.state, pending.error = "failed", "token_exchange"
            return
        if pending.cancel.is_set() or pending.state != "pending":
            return
        try:
            self._save(
                pending,
                "codex_device",
                OAuthCredential(
                    type="oauth",
                    access=access,
                    refresh=refresh,
                    expires=int(time.time() * 1000 + expires_in * 1000),
                    accountId=account,
                ),
            )
        except OAuthFlowError:
            return

    # ----- helpers -------------------------------------------------------------

    def _fail(self, pending: _Pending, category: ErrorCategory) -> Any:
        pending.state, pending.error = "failed", category
        pending.cancel.set()
        raise OAuthFlowError(category)

    def _save(
        self, pending: _Pending, slot: LoginKind, credential: OAuthCredential
    ) -> None:
        try:
            self.store.save_oauth(slot, credential)
        except Exception:
            self._fail(pending, "storage")
        pending.state = "complete"
        pending.oauth_state = pending.verifier = pending.device_auth_id = ""
        pending.cancel.set()
