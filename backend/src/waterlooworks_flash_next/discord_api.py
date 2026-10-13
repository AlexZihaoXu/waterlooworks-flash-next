"""Per-user Discord webhook routes for every signed-in app user.

Session cookie authentication with the shared Origin/JSON/X-WW-Request guards
for mutations; every response is no-store. Webhook URLs are accepted on add
only and never returned, logged or echoed in errors.
"""

import asyncio
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi import Path as ApiPath

from waterlooworks_flash_next.api_models import (
    DiscordWebhookCreate,
    DiscordWebhookList,
    DiscordWebhookUpdate,
    EmptyRequest,
    ErrorResponse,
)
from waterlooworks_flash_next.app_auth import SessionIdentity, require_session
from waterlooworks_flash_next.discord_alerts import DiscordAlerts
from waterlooworks_flash_next.discord_webhooks import (
    MAX_WEBHOOKS_PER_USER,
    DiscordWebhook,
    WebhookDuplicate,
    WebhookLimitReached,
    WebhookNotFound,
    WebhookUnavailable,
    WebhookUrlError,
)
from waterlooworks_flash_next.request_limits import RequestLimiter

ROUTE = "/api/account/discord-webhooks"
TEST_LIMIT = 3
TEST_WINDOW_SECONDS = 60
WebhookId = Annotated[int, ApiPath(ge=1, le=2**62)]
Identity = Annotated[SessionIdentity, Depends(require_session)]
NO_STORE = {"Cache-Control": "no-store"}
UNAVAILABLE = (
    "Discord webhooks are unavailable: the server's encryption key is not "
    "configured. Ask the administrator."
)


def install_discord_webhooks(app: FastAPI, alerts: DiscordAlerts) -> None:
    store = alerts.store
    test_limiter = RequestLimiter(limit=TEST_LIMIT, window_seconds=TEST_WINDOW_SECONDS)

    def responses(*codes: int) -> dict[int | str, dict[str, Any]]:
        return {code: {"model": ErrorResponse} for code in codes}

    def no_store(response: Response) -> None:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"

    def not_found() -> HTTPException:
        return HTTPException(404, "Discord webhook not found.", headers=NO_STORE)

    def unavailable() -> HTTPException:
        return HTTPException(503, UNAVAILABLE, headers=NO_STORE)

    def listing(uid: str) -> DiscordWebhookList:
        return DiscordWebhookList(
            webhooks=store.for_user(uid),
            limit=MAX_WEBHOOKS_PER_USER,
            available=store.available(),
        )

    @app.get(
        ROUTE,
        response_model=DiscordWebhookList,
        operation_id="listDiscordWebhooks",
        tags=["account"],
        description=(
            "The caller's Discord new-job alert webhooks: label, hint, enabled "
            "state and last delivery result. Never the webhook URL."
        ),
    )
    async def list_webhooks(
        identity: Identity, response: Response
    ) -> DiscordWebhookList:
        no_store(response)
        return await asyncio.to_thread(listing, identity.uid)

    @app.post(
        ROUTE,
        status_code=201,
        response_model=DiscordWebhook,
        operation_id="addDiscordWebhook",
        tags=["account"],
        responses=responses(409, 422, 503),
        description=(
            "Add a Discord webhook for new-job alerts. The URL is validated, "
            "encrypted with the backend key and never returned. At most "
            f"{MAX_WEBHOOKS_PER_USER} per account; duplicates are refused."
        ),
    )
    async def add_webhook(
        identity: Identity, body: DiscordWebhookCreate, response: Response
    ) -> DiscordWebhook:
        no_store(response)
        try:
            return await asyncio.to_thread(
                store.add, identity.uid, body.label, body.url.get_secret_value()
            )
        except WebhookUrlError as error:
            raise HTTPException(422, str(error), headers=NO_STORE) from None
        except WebhookLimitReached:
            raise HTTPException(
                409,
                f"You already have {MAX_WEBHOOKS_PER_USER} webhooks; delete one first.",
                headers=NO_STORE,
            ) from None
        except WebhookDuplicate:
            raise HTTPException(
                409, "That webhook is already in your list.", headers=NO_STORE
            ) from None
        except WebhookUnavailable:
            raise unavailable() from None
        except ValueError as error:
            # Only clean_label's fixed text; never the submitted values.
            raise HTTPException(422, str(error), headers=NO_STORE) from None

    @app.patch(
        f"{ROUTE}/{{webhook_id}}",
        response_model=DiscordWebhook,
        operation_id="updateDiscordWebhook",
        tags=["account"],
        responses=responses(404, 422),
        description="Rename the caller's webhook or turn its alerts on or off.",
    )
    async def update_webhook(
        identity: Identity,
        webhook_id: WebhookId,
        body: DiscordWebhookUpdate,
        response: Response,
    ) -> DiscordWebhook:
        no_store(response)
        try:
            return await asyncio.to_thread(
                lambda: store.update(
                    identity.uid, webhook_id, label=body.label, enabled=body.enabled
                )
            )
        except WebhookNotFound:
            raise not_found() from None
        except ValueError as error:
            raise HTTPException(422, str(error), headers=NO_STORE) from None

    @app.delete(
        f"{ROUTE}/{{webhook_id}}",
        response_model=DiscordWebhookList,
        operation_id="deleteDiscordWebhook",
        tags=["account"],
        responses=responses(404),
        description="Delete the caller's webhook; returns the remaining list.",
    )
    async def delete_webhook(
        identity: Identity, webhook_id: WebhookId, response: Response
    ) -> DiscordWebhookList:
        no_store(response)
        try:
            await asyncio.to_thread(store.delete, identity.uid, webhook_id)
        except WebhookNotFound:
            raise not_found() from None
        return await asyncio.to_thread(listing, identity.uid)

    @app.post(
        f"{ROUTE}/{{webhook_id}}/test",
        response_model=DiscordWebhook,
        operation_id="testDiscordWebhook",
        tags=["account"],
        responses=responses(404, 429, 503),
        description=(
            "Send a fixed test message to the caller's webhook and record the "
            f"result. At most {TEST_LIMIT} tests per minute per account."
        ),
    )
    async def test_webhook(
        identity: Identity,
        webhook_id: WebhookId,
        response: Response,
        body: EmptyRequest,
    ) -> DiscordWebhook:
        no_store(response)
        try:
            await asyncio.to_thread(store.view, identity.uid, webhook_id)
        except WebhookNotFound:
            raise not_found() from None
        wait = test_limiter.admit(identity.uid)
        if wait is not None:
            raise HTTPException(
                429,
                "Too many test messages; try again in a minute.",
                headers={"Retry-After": str(wait), **NO_STORE},
            )
        try:
            return await asyncio.to_thread(alerts.send_test, identity.uid, webhook_id)
        except WebhookNotFound:
            raise not_found() from None
        except WebhookUnavailable:
            raise unavailable() from None
