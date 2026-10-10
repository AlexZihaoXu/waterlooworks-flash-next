"""Backend-only Firebase verification and hashed, app-scoped SQLite sessions."""

import base64
import hashlib
import ipaddress
import json
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from urllib.parse import urlsplit

import firebase_admin  # type: ignore[import-untyped]
from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyCookie
from firebase_admin import auth, credentials
from pydantic import BaseModel, ConfigDict, Field

from waterlooworks_flash_next.account_profile import AccountProfile
from waterlooworks_flash_next.runtime_secrets import (
    load_local_environment,
    read_runtime_secret,
    runtime_value,
)

PROJECT_ID = "alex-xu-central"
COOKIE = "ww_session"
MAX_TTL = 3600
# Absolute app-session bound measured from the Google sign-in (`auth_time`).
# Renewal never extends past it; afterwards a fresh Google popup is required.
SESSION_ABSOLUTE_LIFETIME = 12 * 3600
# A renewed session's replaced value stays valid this long for in-flight
# requests and concurrent tabs. A prepared (pre-login) value gets no grace.
ROTATION_GRACE_SECONDS = 30
MAX_PREPARED = 1000  # Live anonymous reservations; the oldest are evicted.
SESSION_COOKIE = APIKeyCookie(name=COOKIE, auto_error=False, scheme_name="AppSession")


class AuthError(Exception):
    """Fixed messages only; never include credentials, claims, or SDK exceptions."""


class BootstrapRefused(AuthError):
    """Empty database and the verified identity is not the pinned owner."""


@dataclass(frozen=True)
class BootstrapOwner:
    """Operator pin for the sole first admin; every configured field must match.

    `email` must be a Google-verified address and compares case-insensitively.
    """

    uid: str | None = None
    email: str | None = None

    @classmethod
    def from_environment(cls) -> "BootstrapOwner":
        uid = (runtime_value("WW_BOOTSTRAP_ADMIN_UID") or "").strip()
        email = (runtime_value("WW_BOOTSTRAP_ADMIN_EMAIL") or "").strip()
        return cls(uid or None, email or None)

    def matches(self, uid: str, verified_email: str | None) -> bool:
        if self.uid is None and self.email is None:
            return False  # Fail closed: never promote an arbitrary first caller.
        if self.uid is not None and (not 1 <= len(self.uid) <= 128 or uid != self.uid):
            return False
        if self.email is not None:
            pinned = self.email.casefold()
            if (
                len(pinned) > 320
                or "@" not in pinned
                or verified_email is None
                or verified_email.strip().casefold() != pinned
            ):
                return False
        return True


def decode_service_account(value: str | None) -> dict[str, Any]:
    try:
        if not value or len(value) > 32768:
            raise ValueError
        raw = base64.b64decode(value, validate=True)
        if base64.b64encode(raw).decode("ascii") != value:
            raise ValueError
        data = json.loads(raw.decode("utf-8"))
        if (
            not isinstance(data, dict)
            or data.get("type") != "service_account"
            or data.get("project_id") != PROJECT_ID
            or data.get("token_uri") != "https://oauth2.googleapis.com/token"
            or not all(
                isinstance(data.get(key), str) and data[key]
                for key in (
                    "private_key",
                    "private_key_id",
                    "client_email",
                    "client_id",
                )
            )
            or not data["client_email"].endswith(
                f"@{PROJECT_ID}.iam.gserviceaccount.com"
            )
        ):
            raise ValueError
        return data
    except Exception:
        raise AuthError("Authentication configuration unavailable.") from None


@dataclass(frozen=True)
class VerifiedGoogleIdentity:
    uid: str
    expires: int
    auth_time: Any
    email: str | None  # Present only when Google marked it verified.


class FirebaseVerifier:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._app: Any = None
        self._loaded = False
        self._lock = threading.Lock()

    def load_environment(self) -> None:
        """Parse the ignored local .env once into a private lookup.

        Never exports values into os.environ; changes need a restart.
        """
        try:
            load_local_environment(self.root / ".env")
            self._loaded = True
        except Exception:
            raise AuthError("Authentication configuration unavailable.") from None

    def ensure_ready(self) -> None:
        if self._app is not None:
            return  # Initialized once; no per-request file I/O or global lock.
        with self._lock:
            try:
                if self._app is not None:
                    return
                if not self._loaded:
                    # Only runtime authentication code may load the user file.
                    self.load_environment()
                if runtime_value("FIREBASE_AUTH_EMULATOR_HOST"):
                    raise ValueError
                data = decode_service_account(
                    read_runtime_secret("FIREBASE_SERVICE_ACCOUNT", max_bytes=32768)
                )
                self._app = firebase_admin.initialize_app(
                    credentials.Certificate(data),
                    {"projectId": PROJECT_ID, "httpTimeout": 10},
                    name="ww-" + secrets.token_hex(16),
                )
            except Exception:
                raise AuthError("Authentication configuration unavailable.") from None

    def verify(self, token: str) -> VerifiedGoogleIdentity:
        return self._verify_claims(token)

    def verify_reauth(self, token: str) -> tuple[str, int, int]:
        verified = self._verify_claims(token)
        uid, expires, auth_time = verified.uid, verified.expires, verified.auth_time
        if type(auth_time) is not int or auth_time > time.time():
            raise AuthError("Google authentication was not accepted.")
        return uid, expires, auth_time

    def _verify_claims(self, token: str) -> "VerifiedGoogleIdentity":
        self.ensure_ready()
        try:
            claims = auth.verify_id_token(
                token, app=self._app, check_revoked=True, clock_skew_seconds=0
            )
            now = int(time.time())
            uid = claims.get("sub")
            expires = claims.get("exp")
            provider = claims.get("firebase", {})
            if (
                not isinstance(uid, str)
                or not 1 <= len(uid) <= 128
                or claims.get("uid") != uid
                or claims.get("aud") != PROJECT_ID
                or claims.get("iss") != f"https://securetoken.google.com/{PROJECT_ID}"
                or type(expires) is not int
                or expires <= now
                or not isinstance(provider, dict)
                or provider.get("sign_in_provider") != "google.com"
            ):
                raise ValueError
            email = claims.get("email")
            verified_email = (
                email
                if isinstance(email, str)
                and len(email) <= 320
                and claims.get("email_verified") is True
                else None
            )
            return VerifiedGoogleIdentity(
                uid, expires, claims.get("auth_time"), verified_email
            )
        except Exception:
            raise AuthError("Google authentication was not accepted.") from None

    def profile(self, uid: str) -> AccountProfile:
        """Caller supplies only a verified app-session UID, never a request UID."""
        self.ensure_ready()
        try:
            user = auth.get_user(uid, app=self._app)
            if user.disabled:
                raise ValueError
            return AccountProfile(
                display_name=user.display_name,
                email=user.email,
                photo_url=user.photo_url,
            )
        except Exception:
            raise AuthError("Account profile unavailable.") from None

    def close(self) -> None:
        if self._app is not None:
            firebase_admin.delete_app(self._app)
            self._app = None


class SessionIdentity(BaseModel):
    uid: str
    role: Literal["admin", "member"]
    expires_at: int


class TokenExchange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    id_token: str = Field(min_length=1, max_length=16384, repr=False)


def _digest(cookie: str | None) -> str:
    if not cookie or len(cookie) != 43:
        return ""
    return hashlib.sha256(cookie.encode()).hexdigest()


@dataclass(frozen=True)
class IssuedSession:
    cookie: str = field(repr=False)
    identity: SessionIdentity


class SessionStore:
    def __init__(self, path: Path, *, bootstrap: BootstrapOwner | None = None) -> None:
        """`bootstrap` defaults to WW_BOOTSTRAP_ADMIN_UID/EMAIL read at issue time."""
        self.path = path
        self.bootstrap = bootstrap

    def _connect(self, *, readonly: bool = False) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path.resolve().as_uri() + ("?mode=ro" if readonly else "?mode=rw"),
            uri=True,
            timeout=5,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        if readonly:
            connection.execute("PRAGMA query_only=ON")
        return connection

    def prepare(self, previous: str | None, *, now: int | None = None) -> str:
        now = int(time.time()) if now is None else now
        cookie = secrets.token_urlsafe(32)
        connection = self._connect()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "DELETE FROM app_sessions WHERE expires_at<=? OR token_hash=?",
                    (now, _digest(previous)),
                )
                # Bound anonymous reservations by evicting the oldest instead of
                # refusing, so a prepare flood cannot block every new sign-in.
                anonymous = connection.execute(
                    "SELECT count(*) FROM app_sessions WHERE uid IS NULL"
                ).fetchone()[0]
                if anonymous >= MAX_PREPARED:
                    connection.execute(
                        "DELETE FROM app_sessions WHERE token_hash IN ("
                        "SELECT token_hash FROM app_sessions WHERE uid IS NULL "
                        "ORDER BY expires_at LIMIT ?)",
                        (anonymous - MAX_PREPARED + 1,),
                    )
                connection.execute(
                    "INSERT INTO app_sessions VALUES (?, NULL, ?)",
                    (_digest(cookie), now + 300),
                )
            return cookie
        finally:
            connection.close()

    def issue(
        self,
        cookie: str | None,
        uid: str,
        token_expiry: int,
        *,
        auth_time: int,
        email: str | None = None,
        now: int | None = None,
    ) -> IssuedSession:
        """Internal primitive: caller must have verified Google identity first.

        `email` is passed only when Google marked it verified. An empty user
        table admits only the pinned owner, as admin; otherwise nobody.
        Every call mints a fresh token in the same transaction: a prepared
        value is deleted (never authenticated), a renewed one keeps only a
        short grace. Logout deletes the presented row, so an in-flight token
        verification cannot resurrect a logged-out session or bootstrap a user.
        """
        now = int(time.time()) if now is None else now
        if type(auth_time) is not int or not (
            now - SESSION_ABSOLUTE_LIFETIME < auth_time <= now
        ):
            raise AuthError("Authentication session expired.")
        expires = min(
            token_expiry, now + MAX_TTL, auth_time + SESSION_ABSOLUTE_LIFETIME
        )
        if not 1 <= len(uid) <= 128 or expires <= now:
            raise AuthError("Authentication session expired.")
        fresh = secrets.token_urlsafe(32)
        connection = self._connect()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                session = connection.execute(
                    "SELECT uid FROM app_sessions WHERE token_hash=? AND expires_at>?",
                    (_digest(cookie), now),
                ).fetchone()
                if session is None or session["uid"] not in (None, uid):
                    raise AuthError("Authentication session expired.")
                user = connection.execute(
                    "SELECT role FROM app_users WHERE uid=?", (uid,)
                ).fetchone()
                if user is None:
                    if connection.execute("SELECT 1 FROM app_users LIMIT 1").fetchone():
                        role = "member"
                    else:
                        owner = self.bootstrap or BootstrapOwner.from_environment()
                        if not owner.matches(uid, email):
                            raise BootstrapRefused(
                                "First administrator must be the configured owner."
                            )
                        role = "admin"
                    connection.execute(
                        "INSERT INTO app_users VALUES (?, ?, ?)", (uid, role, now)
                    )
                else:
                    role = user["role"]
                connection.execute(
                    "INSERT INTO app_sessions VALUES (?, ?, ?)",
                    (_digest(fresh), uid, expires),
                )
                if session["uid"] is None:
                    connection.execute(
                        "DELETE FROM app_sessions WHERE token_hash=?",
                        (_digest(cookie),),
                    )
                else:
                    connection.execute(
                        "UPDATE app_sessions SET expires_at=min(expires_at, ?) "
                        "WHERE token_hash=?",
                        (now + ROTATION_GRACE_SECONDS, _digest(cookie)),
                    )
                    # Renewal continues the same session for the same UID: an
                    # open key-reveal challenge follows the rotated token instead
                    # of cascading away with the old row. It still needs its own
                    # fresh Google re-authentication to be consumed.
                    connection.execute(
                        "UPDATE openai_reauth_challenges SET session_hash=? "
                        "WHERE session_hash=? AND uid=?",
                        (_digest(fresh), _digest(cookie), uid),
                    )
            return IssuedSession(
                fresh,
                SessionIdentity(
                    uid=uid,
                    role=cast(Literal["admin", "member"], role),
                    expires_at=expires,
                ),
            )
        finally:
            connection.close()

    def prepared(self, cookie: str | None) -> bool:
        connection = self._connect(readonly=True)
        try:
            return (
                connection.execute(
                    "SELECT 1 FROM app_sessions WHERE token_hash=? AND expires_at>?",
                    (_digest(cookie), int(time.time())),
                ).fetchone()
                is not None
            )
        finally:
            connection.close()

    def lookup(
        self, cookie: str | None, *, now: int | None = None
    ) -> SessionIdentity | None:
        now = int(time.time()) if now is None else now
        connection = self._connect(readonly=True)
        try:
            row = connection.execute(
                "SELECT u.uid, u.role, s.expires_at FROM app_sessions s "
                "JOIN app_users u ON u.uid=s.uid "
                "WHERE s.token_hash=? AND s.expires_at>?",
                (_digest(cookie), now),
            ).fetchone()
            return SessionIdentity(**dict(row)) if row else None
        finally:
            connection.close()

    def logout(self, cookie: str | None) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    "DELETE FROM app_sessions WHERE token_hash=?", (_digest(cookie),)
                )
        finally:
            connection.close()


@dataclass(frozen=True)
class AuthTransport:
    origins: frozenset[str]
    allow_loopback_http: bool = False

    trusted_proxy: str | None = None

    @classmethod
    def from_environment(cls) -> "AuthTransport":
        try:
            configured = runtime_value("WW_AUTH_ORIGINS")
            origins = frozenset(
                ("https://localhost" if configured is None else configured).split(",")
            )
            allow_http = runtime_value("WW_AUTH_ALLOW_LOOPBACK_HTTP") == "1"
            proxy = runtime_value("WW_AUTH_TRUSTED_PROXY")
            if proxy is not None:
                address = ipaddress.ip_address(proxy)
                proxy = str(address)
                if allow_http or address.is_unspecified or address.is_multicast:
                    raise ValueError
            for origin in origins:
                url = urlsplit(origin)
                if (
                    not url.hostname
                    or not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", url.hostname)
                    or url.username
                    or url.password
                    or url.path
                    or url.query
                    or url.fragment
                    or origin != f"{url.scheme}://{url.netloc}"
                    or url.netloc != url.hostname + (f":{url.port}" if url.port else "")
                    or (
                        url.scheme != "https"
                        and not (
                            allow_http
                            and url.scheme == "http"
                            and url.hostname in {"localhost", "127.0.0.1"}
                        )
                    )
                ):
                    raise ValueError
            return cls(origins, allow_http, proxy)
        except Exception:
            raise AuthError(
                "Authentication transport configuration unavailable."
            ) from None

    def allowed_host(self, request: Request) -> bool:
        hosts = {urlsplit(origin).netloc for origin in self.origins}
        if not self.secure_cookie(request):
            # Preserve the explicit local Vite Host rewrite, never a remote peer.
            hosts.add(request.url.netloc)
        # Health is a fixed credential-free liveness read, not an HTTP auth exception.
        if (
            request.url.path == "/api/health"
            and request.method in {"GET", "HEAD"}
            and request.client is not None
            and request.client.host in {"127.0.0.1", "::1"}
        ):
            hosts.update({"127.0.0.1:8787", "127.0.0.1", "localhost:8787", "localhost"})
        return request.headers.getlist("host") in [[host] for host in hosts]

    def is_https(self, request: Request) -> bool:
        # Uvicorn MUST keep --no-proxy-headers: this checks the actual socket peer.
        return request.url.scheme == "https" or (
            self.trusted_proxy is not None
            and request.client is not None
            and request.client.host == self.trusted_proxy
            and request.headers.getlist("x-forwarded-proto") == ["https"]
        )

    def secure_cookie(self, request: Request) -> bool:
        # A trusted production proxy never weakens Secure cookies. The separate
        # explicit local Vite exception requires an actual loopback socket peer.
        local_http = (
            self.allow_loopback_http
            and request.url.scheme == "http"
            and request.url.hostname in {"localhost", "127.0.0.1"}
            and request.client is not None
            and request.client.host in {"127.0.0.1", "::1"}
        )
        return not local_http


def require_firebase_ready(request: Request) -> None:
    try:
        request.app.state.firebase.ensure_ready()
    except AuthError:
        raise HTTPException(503, "Authentication configuration unavailable.") from None


def require_session(
    request: Request,
    cookie: Annotated[str | None, Security(SESSION_COOKIE)],
    _: None = Depends(require_firebase_ready),
) -> SessionIdentity:
    identity = request.app.state.sessions.lookup(cookie)
    if identity is None:
        raise HTTPException(401, "Sign in to continue.")
    request.state.identity = identity
    return identity  # type: ignore[no-any-return]


def require_admin(
    identity: Annotated[SessionIdentity, Depends(require_session)],
) -> None:
    if identity.role != "admin":
        raise HTTPException(403, "Administrator access required.")
