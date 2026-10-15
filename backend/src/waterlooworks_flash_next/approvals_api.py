"""Admission routes: who is waiting, approve/decline, and the admin tree.

Session cookie authentication with the shared Origin/JSON/X-WW-Request guards
for mutations; every response is no-store. Any approved user reads the
pending list and decides; only the administrator reads the whole tree,
removes approved users and moves declined or removed users back to pending.
Identifiers are the app UIDs the server already keys everything by; emails
appear only where the owner authorised them (pending people, and the admin
tree).
"""

import asyncio
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi import Path as ApiPath

from waterlooworks_flash_next.api_models import EmptyRequest, ErrorResponse
from waterlooworks_flash_next.app_auth import (
    SessionIdentity,
    require_admin,
    require_session,
)
from waterlooworks_flash_next.approvals import (
    ApprovalConflict,
    ApprovalListing,
    ApprovalNotFound,
    ApprovalRefused,
    ApprovalStore,
    ApprovalTree,
)
from waterlooworks_flash_next.request_limits import RequestLimiter

ROUTE = "/api/approvals"
ADMIN_ROUTE = "/api/admin/approvals"
# Modest: a person decides a handful of admissions, not dozens per minute.
DECISION_LIMIT = 30
DECISION_WINDOW_SECONDS = 600
UserId = Annotated[
    str, ApiPath(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
]
Identity = Annotated[SessionIdentity, Depends(require_session)]
NO_STORE = {"Cache-Control": "no-store"}


def install_approvals(app: FastAPI, path: Path) -> None:
    store = ApprovalStore(path)
    limiter = RequestLimiter(
        limit=DECISION_LIMIT, window_seconds=DECISION_WINDOW_SECONDS
    )

    def responses(*codes: int) -> dict[int | str, dict[str, Any]]:
        return {code: {"model": ErrorResponse} for code in codes}

    def no_store(response: Response) -> None:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"

    def admit(uid: str) -> None:
        wait = limiter.admit(uid)
        if wait is not None:
            raise HTTPException(
                429,
                "Too many admission changes; try again later.",
                headers={"Retry-After": str(wait), **NO_STORE},
            )

    def translate(error: Exception) -> HTTPException:
        if isinstance(error, ApprovalNotFound):
            return HTTPException(404, "User not found.", headers=NO_STORE)
        if isinstance(error, ApprovalConflict):
            return HTTPException(409, str(error), headers=NO_STORE)
        return HTTPException(403, str(error), headers=NO_STORE)

    @app.get(
        ROUTE,
        response_model=ApprovalListing,
        operation_id="listApprovals",
        tags=["approvals"],
        description=(
            "Everyone waiting for approval (name, verified email, photo and "
            "when they first signed in) and the people the caller approved."
        ),
    )
    async def listing(identity: Identity, response: Response) -> ApprovalListing:
        no_store(response)
        return await asyncio.to_thread(store.listing, identity.uid)

    @app.post(
        f"{ROUTE}/{{uid}}/approve",
        response_model=ApprovalListing,
        operation_id="approveUser",
        tags=["approvals"],
        responses=responses(404, 409, 429),
        description=(
            "Admit a pending user: they get access now and are listed under the "
            "caller. Returns the updated listing."
        ),
    )
    async def approve(
        identity: Identity, uid: UserId, body: EmptyRequest, response: Response
    ) -> ApprovalListing:
        no_store(response)
        admit(identity.uid)
        try:
            await asyncio.to_thread(store.approve, identity.uid, uid)
        except (ApprovalNotFound, ApprovalConflict, ApprovalRefused) as error:
            raise translate(error) from None
        return await asyncio.to_thread(store.listing, identity.uid)

    @app.post(
        f"{ROUTE}/{{uid}}/decline",
        response_model=ApprovalListing,
        operation_id="declineUser",
        tags=["approvals"],
        responses=responses(404, 409, 429),
        description=(
            "Decline a pending user: their waiting session ends and signing in "
            "again shows a fixed not-approved message until the administrator "
            "moves them back to pending. Returns the updated listing."
        ),
    )
    async def decline(
        identity: Identity, uid: UserId, body: EmptyRequest, response: Response
    ) -> ApprovalListing:
        no_store(response)
        admit(identity.uid)
        try:
            await asyncio.to_thread(store.decline, identity.uid, uid)
        except (ApprovalNotFound, ApprovalConflict, ApprovalRefused) as error:
            raise translate(error) from None
        return await asyncio.to_thread(store.listing, identity.uid)

    admin = [Depends(require_admin)]

    @app.get(
        ADMIN_ROUTE,
        dependencies=admin,
        response_model=ApprovalTree,
        operation_id="getApprovalTree",
        tags=["approvals"],
        description=(
            "Administrator only: the whole admission tree (who approved whom, "
            "with names and emails) plus declined and removed users."
        ),
    )
    async def tree(response: Response) -> ApprovalTree:
        no_store(response)
        return await asyncio.to_thread(store.tree)

    @app.post(
        f"{ADMIN_ROUTE}/{{uid}}/remove",
        dependencies=admin,
        response_model=ApprovalTree,
        operation_id="removeUser",
        tags=["approvals"],
        responses=responses(404, 409, 429),
        description=(
            "Administrator only: an approved user loses access immediately. "
            "Their sessions, agent token, Discord webhooks and posting marks "
            "are deleted; people they approved move up under their approver. "
            "Returns the updated tree."
        ),
    )
    async def remove(
        identity: Identity, uid: UserId, body: EmptyRequest, response: Response
    ) -> ApprovalTree:
        no_store(response)
        admit(identity.uid)
        try:
            await asyncio.to_thread(store.remove, identity.uid, uid)
        except (ApprovalNotFound, ApprovalConflict, ApprovalRefused) as error:
            raise translate(error) from None
        return await asyncio.to_thread(store.tree)

    @app.post(
        f"{ADMIN_ROUTE}/{{uid}}/reinstate",
        dependencies=admin,
        response_model=ApprovalTree,
        operation_id="reinstateUser",
        tags=["approvals"],
        responses=responses(404, 409, 429),
        description=(
            "Administrator only: move a declined or removed user back to "
            "pending so any approved user can admit them again."
        ),
    )
    async def reinstate(
        identity: Identity, uid: UserId, body: EmptyRequest, response: Response
    ) -> ApprovalTree:
        no_store(response)
        admit(identity.uid)
        try:
            await asyncio.to_thread(store.reinstate, identity.uid, uid)
        except (ApprovalNotFound, ApprovalConflict, ApprovalRefused) as error:
            raise translate(error) from None
        return await asyncio.to_thread(store.tree)
