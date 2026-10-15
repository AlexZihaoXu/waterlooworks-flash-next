"""Authentication transport and bounded public session endpoints."""

import asyncio
import sqlite3
import threading
import time
from typing import Annotated, cast

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint

from waterlooworks_flash_next.account_profile import AccountProfile
from waterlooworks_flash_next.agent_api import AGENT_PREFIX
from waterlooworks_flash_next.api_models import RefreshRequest
from waterlooworks_flash_next.app_auth import (
    COOKIE,
    MAX_TTL,
    AccountNotApproved,
    AuthError,
    AuthTransport,
    BootstrapRefused,
    FirebaseVerifier,
    NewAccountHeld,
    SessionIdentity,
    SessionStore,
    TokenExchange,
    require_firebase_ready,
    require_signed_in,
)
from waterlooworks_flash_next.incidents import (
    bind_request,
    record_incident,
    unbind_request,
)


class AuthBusy(Exception):
    """True overload: storage contention or saturated verification slots."""


BUSY_RETRY_AFTER_SECONDS = "2"


def install_auth(
    app: FastAPI, verifier: FirebaseVerifier, sessions: SessionStore
) -> None:
    app.state.firebase = verifier
    app.state.sessions = sessions
    verification_slots = threading.BoundedSemaphore(4)
    profile_slots = threading.BoundedSemaphore(4)

    @app.middleware("http")
    async def transport_guard(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        transport: AuthTransport = app.state.transport

        def refuse(status: int, message: str) -> JSONResponse:
            return JSONResponse(
                status_code=status,
                content={"detail": message},
                headers={"Cache-Control": "no-store"},
            )

        try:
            host_allowed = transport.allowed_host(request)
        except ValueError:
            host_allowed = False
        if not host_allowed:
            return refuse(403, "Host is not allowed.")
        if request.url.path != "/api/health":
            if not transport.is_https(request) and transport.secure_cookie(request):
                return refuse(403, "Secure transport required.")
            origins = request.headers.getlist("origin")
            if len(origins) > 1 or (origins and origins[0] not in transport.origins):
                return refuse(403, "Origin is not allowed.")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            # Bearer-token agent routes ignore cookies, so the cookie CSRF
            # headers do not apply there; JSON and size bounds still do.
            bearer_api = request.url.path.startswith(AGENT_PREFIX)
            if (
                not bearer_api
                and request.headers.get("origin") not in transport.origins
            ):
                return refuse(403, "Origin is required and must be allowed.")
            if (
                request.headers.get("content-type", "").split(";")[0].lower()
                != "application/json"
            ):
                return refuse(415, "Explicit application/json is required.")
            if not bearer_api and request.headers.get("x-ww-request") != "1":
                return refuse(403, "Explicit application request required.")
            size = 0
            chunks = []
            async for chunk in request.stream():
                size += len(chunk)
                if size > 20000:
                    return refuse(413, "Request too large.")
                chunks.append(chunk)
            request._body = b"".join(chunks)
        bound = bind_request(request.scope)
        try:
            response = await call_next(request)
        except Exception as error:
            incident = record_incident("Unhandled", error, request.scope)
            return JSONResponse(
                status_code=500,
                content={
                    "detail": "Request unavailable; diagnostics suppressed. "
                    f"Incident {incident}.",
                    "incident": incident,
                },
                headers={"Cache-Control": "no-store"},
            )
        finally:
            unbind_request(bound)
        if request.url.path.startswith("/api/admin/"):
            # Admin settings, step-up reveal and Duo status: never cacheable,
            # whatever a handler or error path set.
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        else:
            # Default no-store, but respect an explicit handler policy (for
            # example the keyword index's ETag revalidation).
            response.headers.setdefault("Cache-Control", "no-store")
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(AuthError)
    async def authentication_error(request: Request, error: AuthError) -> JSONResponse:
        return JSONResponse(
            status_code=401, content={"detail": "Authentication was not accepted."}
        )

    @app.exception_handler(AuthBusy)
    async def authentication_busy(request: Request, error: AuthBusy) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": "Authentication is busy. Try again shortly."},
            headers={
                "Retry-After": BUSY_RETRY_AFTER_SECONDS,
                "Cache-Control": "no-store",
            },
        )

    @app.exception_handler(BootstrapRefused)
    async def bootstrap_refused(
        request: Request, error: BootstrapRefused
    ) -> JSONResponse:
        # Fixed text; never echo the attempted identity or the configured pin.
        return JSONResponse(
            status_code=403,
            content={
                "detail": "First administrator must be the configured owner.",
                "code": "owner_required",
            },
        )

    @app.exception_handler(NewAccountHeld)
    async def new_account_held(request: Request, error: NewAccountHeld) -> JSONResponse:
        # Fixed text; never echo the attempted identity.
        return JSONResponse(
            status_code=403,
            content={
                "detail": "This account is waiting for approval.",
                "code": "account_held",
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(AccountNotApproved)
    async def account_not_approved(
        request: Request, error: AccountNotApproved
    ) -> JSONResponse:
        # Declined or removed: fixed text, no session, no new admission row.
        return JSONResponse(
            status_code=403,
            content={"detail": "This account is not approved.", "code": "not_approved"},
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(NewAccountHeld)
    async def new_account_held(request: Request, error: NewAccountHeld) -> JSONResponse:
        # Fixed text; never echo the attempted identity.
        return JSONResponse(
            status_code=403,
            content={"detail": "This account is waiting for approval."},
            headers={"Cache-Control": "no-store"},
        )

    def set_cookie(request: Request, response: Response, cookie: str, ttl: int) -> None:
        transport = cast(AuthTransport, app.state.transport)
        response.set_cookie(
            COOKIE,
            cookie,
            max_age=ttl,
            httponly=True,
            secure=transport.secure_cookie(request),
            samesite="strict",
            path="/api",
        )

    @app.post(
        "/api/auth/prepare",
        dependencies=[Depends(require_firebase_ready)],
        operation_id="prepareAuth",
    )
    def prepare(
        request: Request, response: Response, body: RefreshRequest
    ) -> dict[str, bool]:
        try:
            cookie = sessions.prepare(request.cookies.get(COOKIE))
        except sqlite3.OperationalError:
            raise AuthBusy from None  # e.g. database locked past its timeout.
        set_cookie(request, response, cookie, MAX_TTL)
        return {"ready": True}

    @app.post(
        "/api/auth/exchange",
        response_model=SessionIdentity,
        dependencies=[Depends(require_firebase_ready)],
        operation_id="exchangeAuth",
    )
    def exchange(
        request: Request, response: Response, body: TokenExchange
    ) -> SessionIdentity:
        cookie = request.cookies.get(COOKIE)
        if not cookie or not sessions.prepared(cookie):
            raise HTTPException(401, "Prepare sign-in first.")
        if not verification_slots.acquire(blocking=False):
            raise AuthBusy
        try:
            verified = verifier.verify(body.id_token)
            issued = sessions.issue(
                cookie,
                verified.uid,
                verified.expires,
                auth_time=verified.auth_time,
                email=verified.email,
                profile=verified.profile,
            )
        except sqlite3.OperationalError:
            raise AuthBusy from None
        finally:
            verification_slots.release()
        # Always a freshly minted value: login and renewal both rotate.
        set_cookie(
            request,
            response,
            issued.cookie,
            max(0, issued.identity.expires_at - int(time.time())),
        )
        return issued.identity

    @app.get(
        "/api/auth/session",
        response_model=SessionIdentity,
        operation_id="getAuthSession",
    )
    def session(
        identity: Annotated[SessionIdentity, Depends(require_signed_in)],
    ) -> SessionIdentity:
        # Pending users read their own admission state here; nothing else.
        return identity

    @app.get(
        "/api/auth/profile",
        response_model=AccountProfile,
        operation_id="getAuthProfile",
        description=(
            "Optional display fields for the current verified session only; "
            "never cached."
        ),
    )
    async def profile(
        request: Request,
        identity: Annotated[SessionIdentity, Depends(require_signed_in)],
    ) -> AccountProfile:
        if request.query_params:
            raise HTTPException(400, "Profile parameters are not accepted.")
        if not profile_slots.acquire(blocking=False):
            raise HTTPException(429, "Account profile is busy. Try again later.")
        uid = identity.uid

        def lookup() -> AccountProfile | None:
            try:
                return verifier.profile(uid)
            except Exception:
                return None  # Never expose SDK diagnostics or retain failed results.
            finally:
                profile_slots.release()

        # SDK calls have a 10-second HTTP timeout and finite retries. Bound the
        # response separately, retaining the slot until actual I/O finishes so
        # timed-out callers cannot create unbounded background lookups.
        pending = asyncio.create_task(asyncio.to_thread(lookup))
        try:
            result = await asyncio.wait_for(asyncio.shield(pending), timeout=12)
        except TimeoutError:
            raise HTTPException(503, "Account profile unavailable.") from None
        current = await asyncio.to_thread(sessions.lookup, request.cookies.get(COOKIE))
        if current is None or current.uid != uid:
            raise HTTPException(401, "Sign in to continue.")
        if result is None:
            raise HTTPException(503, "Account profile unavailable.")
        return result

    @app.post("/api/auth/logout", operation_id="logoutAuth")
    def logout(
        request: Request, response: Response, body: RefreshRequest
    ) -> dict[str, bool]:
        sessions.logout(request.cookies.get(COOKIE))
        response.delete_cookie(
            COOKIE,
            path="/api",
            httponly=True,
            secure=app.state.transport.secure_cookie(request),
            samesite="strict",
        )
        return {"signed_out": True}
