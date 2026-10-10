"""Authentication transport and bounded public session endpoints."""

import asyncio
import threading
import time
from typing import Annotated, cast

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint

from waterlooworks_flash_next.account_profile import AccountProfile
from waterlooworks_flash_next.api_models import RefreshRequest
from waterlooworks_flash_next.app_auth import (
    COOKIE,
    MAX_TTL,
    AuthError,
    AuthTransport,
    BootstrapRefused,
    FirebaseVerifier,
    SessionIdentity,
    SessionStore,
    TokenExchange,
    require_firebase_ready,
    require_session,
)


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
            if request.headers.get("origin") not in transport.origins:
                return refuse(403, "Origin is required and must be allowed.")
            if (
                request.headers.get("content-type", "").split(";")[0].lower()
                != "application/json"
            ):
                return refuse(415, "Explicit application/json is required.")
            if request.headers.get("x-ww-request") != "1":
                return refuse(403, "Explicit application request required.")
            size = 0
            chunks = []
            async for chunk in request.stream():
                size += len(chunk)
                if size > 20000:
                    return refuse(413, "Request too large.")
                chunks.append(chunk)
            request._body = b"".join(chunks)
        try:
            response = await call_next(request)
        except Exception:
            return refuse(500, "Request unavailable; diagnostics suppressed.")
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(AuthError)
    async def authentication_error(request: Request, error: AuthError) -> JSONResponse:
        return JSONResponse(
            status_code=401, content={"detail": "Authentication was not accepted."}
        )

    @app.exception_handler(BootstrapRefused)
    async def bootstrap_refused(
        request: Request, error: BootstrapRefused
    ) -> JSONResponse:
        # Fixed text; never echo the attempted identity or the configured pin.
        return JSONResponse(
            status_code=403,
            content={"detail": "First administrator must be the configured owner."},
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
        cookie = sessions.prepare(request.cookies.get(COOKIE))
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
            raise HTTPException(429, "Authentication is busy. Try again later.")
        try:
            verified = verifier.verify(body.id_token)
            identity = sessions.issue(
                cookie, verified.uid, verified.expires, email=verified.email
            )
        finally:
            verification_slots.release()
        set_cookie(
            request, response, cookie, max(0, identity.expires_at - int(time.time()))
        )
        return identity

    @app.get(
        "/api/auth/session",
        response_model=SessionIdentity,
        operation_id="getAuthSession",
    )
    def session(
        identity: Annotated[SessionIdentity, Depends(require_session)],
    ) -> SessionIdentity:
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
        identity: Annotated[SessionIdentity, Depends(require_session)],
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
