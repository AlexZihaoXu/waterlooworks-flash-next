"""Admin-only settings/step-up routes. Saving never invokes a provider."""

import asyncio
import threading
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from waterlooworks_flash_next.api_models import RefreshRequest
from waterlooworks_flash_next.app_auth import COOKIE, FirebaseVerifier, require_admin
from waterlooworks_flash_next.credentials import CredentialError, CredentialStatus
from waterlooworks_flash_next.openai_settings import (
    OpenAIKey,
    OpenAISettingsStore,
    RevealChallenge,
    RevealedKey,
    RevealRequest,
)


def install_openai_settings(
    app: FastAPI, path: Path, verifier: FirebaseVerifier
) -> None:
    store = OpenAISettingsStore(path)
    slots = threading.BoundedSemaphore(4)

    @app.exception_handler(CredentialError)
    async def unavailable(request: Request, error: CredentialError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Credential settings unavailable; check the backend key."
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.get(
        "/api/admin/openai-key",
        dependencies=[Depends(require_admin)],
        response_model=CredentialStatus,
        operation_id="getOpenAIKeyStatus",
    )
    def status() -> CredentialStatus:
        return store.status()

    @app.post(
        "/api/admin/openai-key",
        dependencies=[Depends(require_admin)],
        response_model=CredentialStatus,
        operation_id="saveOpenAIKey",
    )
    def save(body: OpenAIKey) -> CredentialStatus:
        return store.save(body)

    @app.post(
        "/api/admin/openai-key/reveal/prepare",
        dependencies=[Depends(require_admin)],
        response_model=RevealChallenge,
        operation_id="prepareOpenAIKeyReveal",
    )
    async def prepare(request: Request, body: RefreshRequest) -> RevealChallenge:
        cookie, uid = request.cookies.get(COOKIE), request.state.identity.uid
        challenge = await asyncio.to_thread(store.prepare_reveal, cookie, uid)
        # Firebase auth_time is second-resolution. A second explicit user gesture
        # opens the popup after this wait, never accepting a pre-issuance token.
        await asyncio.sleep(max(0, challenge.not_before - time.time()))
        await asyncio.to_thread(
            store.validate_challenge, cookie, uid, challenge.challenge
        )
        return challenge

    @app.post(
        "/api/admin/openai-key/reveal",
        dependencies=[Depends(require_admin)],
        response_model=RevealedKey,
        operation_id="revealOpenAIKey",
    )
    async def reveal(request: Request, body: RevealRequest) -> RevealedKey:
        cookie, expected_uid = request.cookies.get(COOKIE), request.state.identity.uid
        nonce = body.challenge.get_secret_value()
        await asyncio.to_thread(store.validate_challenge, cookie, expected_uid, nonce)
        if not slots.acquire(blocking=False):
            raise HTTPException(429, "Verification is busy. Try again later.")

        def verify() -> tuple[str, int, int]:
            try:
                return verifier.verify_reauth(body.id_token.get_secret_value())
            finally:
                slots.release()

        # Retain the slot until bounded SDK I/O actually completes. A timeout never
        # starts decryption in a background task or sends a late plaintext response.
        pending = asyncio.create_task(asyncio.to_thread(verify))
        try:
            uid, expiry, auth_time = await asyncio.wait_for(asyncio.shield(pending), 12)
        except TimeoutError:
            pending.add_done_callback(
                lambda task: task.exception() if not task.cancelled() else None
            )
            raise HTTPException(
                503, "Verification timed out. Prepare reveal again."
            ) from None
        if uid != expected_uid:
            raise HTTPException(403, "Verify the same administrator account.")
        value = await asyncio.to_thread(
            store.consume_reveal, cookie, uid, nonce, auth_time, expiry
        )
        return RevealedKey(api_key=value)
