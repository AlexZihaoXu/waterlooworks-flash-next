"""Per-user account routes for every signed-in app user (member or admin).

Session cookie authentication with the shared Origin/JSON/X-WW-Request guards
for mutations. The API token plaintext appears only in the reset response.
"""

import asyncio
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi import Path as ApiPath

from waterlooworks_flash_next.api_models import (
    ApiTokenIssued,
    EmptyRequest,
    ErrorResponse,
    PostingMarksResponse,
    PostingStateRequest,
    PostingViewedRequest,
)
from waterlooworks_flash_next.api_tokens import ApiTokenStatus, TokenStore
from waterlooworks_flash_next.app_auth import SessionIdentity, require_session
from waterlooworks_flash_next.posting_marks import (
    MarkStore,
    PostingMark,
    UnknownPosting,
)
from waterlooworks_flash_next.request_limits import RequestLimiter

TOKEN_ROUTE = "/api/account/api-token"
STATES_ROUTE = "/api/account/posting-states"
RESET_LIMIT = 5
RESET_WINDOW_SECONDS = 600
PostingId = Annotated[str, ApiPath(pattern=r"^[0-9]+$")]
Identity = Annotated[SessionIdentity, Depends(require_session)]


def install_account(app: FastAPI, path: Path) -> None:
    tokens = TokenStore(path)
    marks = MarkStore(path)
    reset_limiter = RequestLimiter(
        limit=RESET_LIMIT, window_seconds=RESET_WINDOW_SECONDS
    )
    not_found: dict[int | str, dict[str, Any]] = {404: {"model": ErrorResponse}}

    def no_store(response: Response) -> None:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"

    @app.get(
        TOKEN_ROUTE,
        response_model=ApiTokenStatus,
        operation_id="getApiToken",
        tags=["account"],
        description=(
            "Safe metadata about the caller's agent API token: whether one "
            "exists, its hint, creation and last-use times. Never the token."
        ),
    )
    async def token_status(identity: Identity, response: Response) -> ApiTokenStatus:
        no_store(response)
        return await asyncio.to_thread(tokens.status, identity.uid)

    @app.post(
        f"{TOKEN_ROUTE}/reset",
        response_model=ApiTokenIssued,
        operation_id="resetApiToken",
        tags=["account"],
        responses={429: {"model": ErrorResponse}},
        description=(
            "Mint a new agent API token for the caller and return its plaintext "
            "exactly once. Any previous token stops working immediately. "
            f"At most {RESET_LIMIT} resets per {RESET_WINDOW_SECONDS // 60} minutes."
        ),
    )
    async def reset_token(
        identity: Identity, response: Response, body: EmptyRequest
    ) -> ApiTokenIssued:
        no_store(response)
        wait = reset_limiter.admit(identity.uid)
        if wait is not None:
            raise HTTPException(
                429,
                "Too many token resets; try again later.",
                headers={"Retry-After": str(wait), "Cache-Control": "no-store"},
            )
        issued = await asyncio.to_thread(tokens.reset, identity.uid)
        return ApiTokenIssued(
            token=issued.token, hint=issued.hint, created_at=issued.created_at
        )

    @app.delete(
        TOKEN_ROUTE,
        response_model=ApiTokenStatus,
        operation_id="revokeApiToken",
        tags=["account"],
        description="Revoke the caller's agent API token immediately.",
    )
    async def revoke_token(identity: Identity, response: Response) -> ApiTokenStatus:
        no_store(response)
        await asyncio.to_thread(tokens.revoke, identity.uid)
        return ApiTokenStatus(configured=False)

    @app.get(
        STATES_ROUTE,
        response_model=PostingMarksResponse,
        operation_id="getPostingMarks",
        tags=["account"],
        description="All of the caller's posting states and viewed times.",
    )
    async def posting_marks(
        identity: Identity, response: Response
    ) -> PostingMarksResponse:
        no_store(response)
        return PostingMarksResponse(
            marks=await asyncio.to_thread(marks.read_all, identity.uid)
        )

    @app.put(
        f"{STATES_ROUTE}/{{posting_id}}",
        response_model=PostingMark,
        responses=not_found,
        operation_id="setPostingState",
        tags=["account"],
        description="Set or clear (null) the caller's state for one saved posting.",
    )
    async def set_state(
        identity: Identity,
        posting_id: PostingId,
        body: PostingStateRequest,
        response: Response,
    ) -> PostingMark:
        no_store(response)
        try:
            return await asyncio.to_thread(
                marks.set_state, identity.uid, posting_id, body.state
            )
        except UnknownPosting:
            raise HTTPException(404, "Stored posting not found.") from None

    @app.put(
        f"{STATES_ROUTE}/{{posting_id}}/viewed",
        response_model=PostingMark,
        responses=not_found,
        operation_id="setPostingViewed",
        tags=["account"],
        description=(
            "Mark one saved posting viewed (keeps the first view time) or clear "
            "the mark."
        ),
    )
    async def set_viewed(
        identity: Identity,
        posting_id: PostingId,
        body: PostingViewedRequest,
        response: Response,
    ) -> PostingMark:
        no_store(response)
        try:
            return await asyncio.to_thread(
                marks.set_viewed, identity.uid, posting_id, body.viewed
            )
        except UnknownPosting:
            raise HTTPException(404, "Stored posting not found.") from None
