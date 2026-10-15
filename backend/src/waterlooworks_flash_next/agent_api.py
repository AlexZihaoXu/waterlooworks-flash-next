"""Bearer-token agent API: read, search and mark postings for one app user.

``Authorization: Bearer wwf_…`` only: cookies and query-string tokens are
never consulted, so the session CSRF headers do not apply. Every request
re-resolves the token to a *current* app user and role, is rate limited per
token, and answers bad or missing tokens with one generic 401 that never
echoes the presented value. Nothing here starts, schedules or configures a
refresh, or exposes admin-only data.
"""

import asyncio
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Response
from fastapi import Path as ApiPath
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from waterlooworks_flash_next.agent_search import (
    PostingQuery,
    paginate,
    select_postings,
    summarize,
)
from waterlooworks_flash_next.analysis_read import read_analysis
from waterlooworks_flash_next.api_models import (
    AgentIdentityResponse,
    AgentPostingDetail,
    AgentPostingPage,
    AgentRefreshStatus,
    ErrorResponse,
    PostingResponse,
    PostingStateRequest,
    PostingViewedRequest,
)
from waterlooworks_flash_next.api_tokens import AgentIdentity, TokenStore, token_digest
from waterlooworks_flash_next.app_auth import FirebaseVerifier
from waterlooworks_flash_next.content import StoredPostingContent
from waterlooworks_flash_next.content_storage import (
    FORMATTING_JOIN,
    formatting_from_row,
)
from waterlooworks_flash_next.incidents import suppressed_failure
from waterlooworks_flash_next.posting_marks import (
    MarkStore,
    PostingMark,
    UnknownPosting,
)
from waterlooworks_flash_next.refresh import RefreshService
from waterlooworks_flash_next.request_limits import RequestLimiter
from waterlooworks_flash_next.rich_text import FORMAT_VERSION

AGENT_PREFIX = "/api/agent/v1/"
RATE_LIMIT = 60
RATE_WINDOW_SECONDS = 60
BEARER = HTTPBearer(
    auto_error=False,
    scheme_name="AgentToken",
    description=(
        "Per-user agent token from the account settings (POST "
        "/api/account/api-token/reset), sent as `Authorization: Bearer wwf_…`."
    ),
)
PostingId = Annotated[str, ApiPath(pattern=r"^[0-9]+$")]
NOT_FOUND = "Stored posting not found."


def install_agent_api(
    app: FastAPI,
    *,
    path: Path,
    postings: Callable[[], list[PostingResponse]],
    service: RefreshService,
    verifier: FirebaseVerifier,
    guide: Path | None = None,
) -> None:
    """``postings`` returns the cached, identity-free member workspace listing.

    ``guide`` is the tracked ``docs/agent-api.md``; its text describes the
    public agent schema and is served verbatim at ``/guide``.
    """
    tokens = TokenStore(path)
    marks = MarkStore(path)
    limiter = RequestLimiter(limit=RATE_LIMIT, window_seconds=RATE_WINDOW_SECONDS)
    profile_slots = threading.BoundedSemaphore(4)
    no_store_headers = {"Cache-Control": "no-store"}

    def refused() -> HTTPException:
        return HTTPException(
            401,
            "Agent token was not accepted.",
            headers={"WWW-Authenticate": "Bearer", **no_store_headers},
        )

    async def require_agent(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(BEARER)],
    ) -> AgentIdentity:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise refused()
        presented = credentials.credentials
        wait = limiter.admit(token_digest(presented))
        if wait is not None:
            raise HTTPException(
                429,
                "Agent rate limit reached; slow down.",
                headers={"Retry-After": str(wait), **no_store_headers},
            )
        identity = await asyncio.to_thread(tokens.authenticate, presented)
        if identity is None:
            raise refused()
        return identity

    Agent = Annotated[AgentIdentity, Depends(require_agent)]
    errors: dict[int | str, dict[str, Any]] = {
        401: {
            "model": ErrorResponse,
            "description": "Missing, malformed, revoked or replaced token.",
            "headers": {"WWW-Authenticate": {"schema": {"type": "string"}}},
        },
        429: {
            "model": ErrorResponse,
            "description": f"More than {RATE_LIMIT} requests per minute for one token.",
            "headers": {"Retry-After": {"schema": {"type": "integer"}}},
        },
    }
    not_found: dict[int | str, dict[str, Any]] = {404: {"model": ErrorResponse}}
    router = APIRouter(
        prefix=AGENT_PREFIX.rstrip("/"), tags=["agent"], responses=errors
    )

    def no_store(response: Response) -> None:
        response.headers["Cache-Control"] = "no-store"

    def read_connection() -> sqlite3.Connection:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    @router.get(
        "/me",
        response_model=AgentIdentityResponse,
        operation_id="getAgentIdentity",
        description=(
            "Whose behalf this token acts on: the app user's UID, current role "
            "and the display profile the app shows them (null when unavailable)."
        ),
    )
    async def me(agent: Agent, response: Response) -> AgentIdentityResponse:
        no_store(response)
        uid = agent.uid
        profile = None
        if profile_slots.acquire(blocking=False):

            def lookup() -> Any:
                try:
                    return verifier.profile(uid)
                except Exception:
                    return None  # Fixed null; never SDK diagnostics.
                finally:
                    profile_slots.release()

            pending = asyncio.create_task(asyncio.to_thread(lookup))
            try:
                profile = await asyncio.wait_for(asyncio.shield(pending), timeout=12)
            except TimeoutError:
                profile = None
        return AgentIdentityResponse(uid=uid, role=agent.role, profile=profile)

    @router.get(
        "/refresh",
        response_model=AgentRefreshStatus,
        operation_id="getAgentRefreshStatus",
        description=(
            "When the saved listings were last refreshed, whether a refresh is "
            "running now and when the next scheduled one is due (null if none). "
            "Read-only: agents cannot start or schedule refreshes."
        ),
    )
    async def refresh_status(agent: Agent, response: Response) -> AgentRefreshStatus:
        no_store(response)

        def read() -> tuple[str | None, str | None]:
            connection = read_connection()
            try:
                observed = connection.execute(
                    "SELECT max(last_seen_at) FROM postings"
                ).fetchone()[0]
                finished = connection.execute(
                    "SELECT max(finished_at) FROM collection_runs"
                ).fetchone()[0]
                return observed, finished
            finally:
                connection.close()

        try:
            observed, finished = await asyncio.to_thread(read)
            snapshot = await asyncio.to_thread(service.snapshot)
        except Exception:
            raise suppressed_failure(
                "Refresh status is unavailable; diagnostics suppressed."
            ) from None
        return AgentRefreshStatus(
            running=snapshot.status == "running",
            status=snapshot.status,
            last_refreshed_at=observed,
            last_run_finished_at=finished,
            next_scheduled_at=snapshot.next_scheduled_at,
        )

    @router.get(
        "/postings",
        response_model=AgentPostingPage,
        operation_id="listAgentPostings",
        description=(
            "Search, filter, sort and page the saved postings with the caller's "
            "state and viewed marks. Filters are OR within a parameter and AND "
            "across parameters; unknown parameters are refused."
        ),
    )
    async def list_postings(
        agent: Agent, response: Response, query: Annotated[PostingQuery, Query()]
    ) -> AgentPostingPage:
        no_store(response)
        try:
            items = await asyncio.to_thread(postings)
            user_marks = await asyncio.to_thread(marks.read_all, agent.uid)
        except HTTPException:
            raise
        except Exception:
            raise suppressed_failure(
                "Stored workspace is unavailable; diagnostics suppressed."
            ) from None
        page = paginate(
            select_postings(items, user_marks, query), query.page, query.page_size
        )
        return AgentPostingPage(
            page=page.page,
            page_size=page.page_size,
            total=page.total,
            total_pages=page.total_pages,
            postings=[
                summarize(item, user_marks.get(item.posting_id)) for item in page.items
            ],
        )

    @router.get(
        "/postings/{posting_id}",
        response_model=AgentPostingDetail,
        responses=not_found,
        operation_id="getAgentPosting",
        description=(
            "One saved posting with its plain-text detail content and analysis, "
            "exactly as a member sees it. Reading never marks it viewed."
        ),
    )
    async def posting_detail(
        agent: Agent, posting_id: PostingId, response: Response
    ) -> AgentPostingDetail:
        no_store(response)

        def read() -> AgentPostingDetail:
            summary = next(
                (item for item in postings() if item.posting_id == posting_id), None
            )
            if summary is None:
                raise UnknownPosting(NOT_FOUND)
            mark = marks.read_all(agent.uid).get(posting_id)
            connection = read_connection()
            try:
                connection.execute("BEGIN")
                row = connection.execute(
                    "SELECT c.content_json, c.last_seen_at, f.blocks_json "
                    "FROM postings p "
                    "LEFT JOIN posting_contents c ON c.posting_id=p.posting_id "
                    f"{FORMATTING_JOIN} WHERE p.posting_id=?",
                    (FORMAT_VERSION, posting_id),
                ).fetchone()
                if row is None:
                    raise UnknownPosting(NOT_FOUND)
                analysis = read_analysis(connection, posting_id)
            finally:
                connection.close()
            content = (
                StoredPostingContent.model_validate_json(row["content_json"])
                if row["content_json"] is not None
                else None
            )
            return AgentPostingDetail(
                **summarize(summary, mark).model_dump(),
                content=content,
                content_observed_at=row["last_seen_at"],
                formatting=formatting_from_row(content, row["blocks_json"])
                if content is not None
                else None,
                analysis=analysis,
            )

        try:
            return await asyncio.to_thread(read)
        except (UnknownPosting, KeyError):
            raise HTTPException(404, NOT_FOUND, headers=no_store_headers) from None
        except HTTPException:
            raise
        except Exception:
            raise suppressed_failure(
                "Stored posting is unavailable; diagnostics suppressed.",
                headers=no_store_headers,
            ) from None

    @router.put(
        "/postings/{posting_id}/state",
        response_model=PostingMark,
        responses=not_found,
        operation_id="setAgentPostingState",
        description=(
            "Set the caller's state for one posting to interested, applied or "
            "not_interested, or clear it with null."
        ),
    )
    async def set_state(
        agent: Agent,
        posting_id: PostingId,
        body: PostingStateRequest,
        response: Response,
    ) -> PostingMark:
        no_store(response)
        try:
            return await asyncio.to_thread(
                marks.set_state, agent.uid, posting_id, body.state
            )
        except UnknownPosting:
            raise HTTPException(404, NOT_FOUND, headers=no_store_headers) from None

    @router.put(
        "/postings/{posting_id}/viewed",
        response_model=PostingMark,
        responses=not_found,
        operation_id="setAgentPostingViewed",
        description=(
            "Explicitly mark one posting viewed (true keeps the first view time) "
            "or clear the mark (false). Reads never set this."
        ),
    )
    async def set_viewed(
        agent: Agent,
        posting_id: PostingId,
        body: PostingViewedRequest,
        response: Response,
    ) -> PostingMark:
        no_store(response)
        try:
            return await asyncio.to_thread(
                marks.set_viewed, agent.uid, posting_id, body.viewed
            )
        except UnknownPosting:
            raise HTTPException(404, NOT_FOUND, headers=no_store_headers) from None

    def guide_text() -> str | None:
        try:
            return guide.read_text(encoding="utf-8") if guide else None
        except OSError:
            return None

    public = APIRouter(prefix=AGENT_PREFIX.rstrip("/"), tags=["agent"])
    schema_cache: list[dict[str, Any]] = []

    @public.get(
        "/openapi.json",
        operation_id="getAgentOpenApi",
        response_class=JSONResponse,
        description=(
            "Public OpenAPI 3 schema of the agent API only (no admin or "
            "session routes), with the agent guide as its description. "
            "No token needed; every data route still requires one."
        ),
    )
    def agent_openapi() -> dict[str, Any]:
        if not schema_cache:
            schema_cache.append(agent_schema(app.openapi(), guide_text()))
        return schema_cache[0]

    @public.get(
        "/guide",
        operation_id="getAgentGuide",
        response_class=PlainTextResponse,
        responses={200: {"content": {"text/markdown": {}}}},
        description="Public Markdown guide to the agent API (docs/agent-api.md).",
    )
    def agent_guide() -> PlainTextResponse:
        text = guide_text()
        if text is None:
            raise HTTPException(404, "Agent guide is unavailable.")
        return PlainTextResponse(text, media_type="text/markdown; charset=utf-8")

    app.include_router(router)
    app.include_router(public)


def _schema_refs(node: object) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                found.add(value.removeprefix("#/components/schemas/"))
            else:
                found |= _schema_refs(value)
    elif isinstance(node, list):
        for value in node:
            found |= _schema_refs(value)
    return found


def agent_schema(full: dict[str, Any], guide: str | None) -> dict[str, Any]:
    """The agent paths of the app schema plus only the components they reach."""
    paths = {
        path: operations
        for path, operations in full["paths"].items()
        if path.startswith(AGENT_PREFIX)
    }
    available = full.get("components", {}).get("schemas", {})
    pending, kept = _schema_refs(paths), {}
    while pending:
        name = pending.pop()
        if name in kept or name not in available:
            continue
        kept[name] = available[name]
        pending |= _schema_refs(kept[name])
    return {
        "openapi": full["openapi"],
        "info": {
            "title": "WaterlooWorks Flash agent API",
            "version": full["info"]["version"],
            "description": guide or "Agent API: see docs/agent-api.md.",
        },
        "paths": paths,
        "components": {
            "schemas": dict(sorted(kept.items())),
            "securitySchemes": {
                "AgentToken": full["components"]["securitySchemes"]["AgentToken"]
            },
        },
    }
