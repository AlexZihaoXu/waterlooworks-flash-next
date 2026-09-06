"""Loopback-only local API. Swagger UI /doc; OpenAPI /doc/openapi.json."""

import asyncio
import os
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from waterlooworks_flash_next.api_models import (
    ErrorResponse,
    HealthResponse,
    ListingRevision,
    PostingResponse,
    RefreshAccepted,
    RefreshRequest,
    RefreshSnapshot,
    WorkspaceResponse,
)
from waterlooworks_flash_next.database import (
    SCHEMA_VERSION,
    StoredListing,
    open_database,
)
from waterlooworks_flash_next.refresh import RefreshConflict, RefreshService


def create_app(database_path: Path | None = None) -> FastAPI:
    path = database_path or Path(
        os.environ.get("WW_DATABASE", "data/waterlooworks.sqlite3")
    )
    # Test-specific paths never enable authentication through environment inheritance.
    enabled = database_path is None and os.environ.get("WW_ENABLE_REFRESH") == "1"
    root = Path(os.environ.get("WW_PROJECT_ROOT", str(Path.cwd())))
    service = RefreshService(path, root / ".credentials.json" if enabled else None)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        open_database(path).close()
        service.restore()
        yield

    app = FastAPI(
        title="WaterlooWorks Flash Next local API",
        version="0.1.0",
        description=(
            "Loopback-only listings API. Manual, single-flight refresh with "
            "a 300-second post-run cooldown. No individual detail collection. "
            "Never expose this service publicly."
        ),
        responses={
            403: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
        },
        docs_url="/doc",
        openapi_url="/doc/openapi.json",
        redoc_url=None,
        lifespan=lifespan,
    )
    errors: dict[int | str, dict[str, Any]] = {
        code: {"model": ErrorResponse} for code in (403, 409, 415, 422, 429, 500, 503)
    }

    @app.middleware("http")
    async def local_guard(request: Request, call_next: object) -> Response:
        from typing import cast

        from starlette.middleware.base import RequestResponseEndpoint

        host = request.headers.get("host", "").split(":")[0]
        hosts = {"localhost", "127.0.0.1"}
        if database_path is not None:
            hosts.add("testserver")
        if host not in hosts:
            return JSONResponse(
                status_code=403, content={"detail": "Local host required."}
            )
        if request.method == "POST":
            allowed = {
                f"http://{name}:{port}"
                for name in ("localhost", "127.0.0.1")
                for port in (5173, 8787)
            }
            origin = request.headers.get("origin")
            if origin is not None and origin not in allowed:
                return JSONResponse(
                    status_code=403, content={"detail": "Origin is not allowed."}
                )
            if (
                request.headers.get("content-type", "").split(";")[0].lower()
                != "application/json"
            ):
                return JSONResponse(
                    status_code=415,
                    content={"detail": "Explicit application/json is required."},
                )
        return await cast(RequestResponseEndpoint, call_next)(request)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"detail": "Invalid request; input diagnostics suppressed."},
        )

    @app.get("/api/health", response_model=HealthResponse, operation_id="getHealth")
    def health() -> HealthResponse:
        return HealthResponse(schema_version=SCHEMA_VERSION)

    @app.get(
        "/api/workspace",
        response_model=WorkspaceResponse,
        responses=errors,
        operation_id="getWorkspace",
    )
    def workspace(response: Response) -> WorkspaceResponse:
        response.headers["Cache-Control"] = "no-store"
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            history: dict[str, list[ListingRevision]] = {}
            for row in connection.execute(
                "SELECT * FROM posting_revisions ORDER BY revision_id DESC"
            ):
                history.setdefault(row["posting_id"], []).append(
                    ListingRevision(
                        revision_id=row["revision_id"],
                        content_hash=row["content_hash"],
                        hash_version=row["hash_version"],
                        observed_at=row["observed_at"],
                        run_id=row["run_id"],
                        snapshot=StoredListing.model_validate_json(row["listing_json"]),
                    )
                )
            postings = []
            for row in connection.execute(
                "SELECT * FROM postings ORDER BY last_seen_at DESC, posting_id"
            ):
                listing = StoredListing.model_validate_json(row["listing_json"])
                postings.append(
                    PostingResponse(
                        posting_id=row["posting_id"],
                        listing=listing,
                        first_seen_at=row["first_seen_at"],
                        last_seen_at=row["last_seen_at"],
                        location=listing.city
                        if isinstance(listing.city, str)
                        else None,
                        listing_revisions=history.get(row["posting_id"], []),
                    )
                )
            return WorkspaceResponse(
                postings=postings, manual_refresh_available=enabled
            )
        except Exception:
            raise HTTPException(
                500, "Stored workspace is unavailable; diagnostics suppressed."
            ) from None
        finally:
            connection.close()

    @app.get("/api/refresh", response_model=RefreshSnapshot, operation_id="getRefresh")
    def refresh_status(response: Response) -> RefreshSnapshot:
        response.headers["Cache-Control"] = "no-store"
        return service.snapshot()

    @app.post(
        "/api/refresh",
        status_code=202,
        response_model=RefreshAccepted,
        responses=errors,
        operation_id="startRefresh",
    )
    def start_refresh(
        request: Request, response: Response, body: RefreshRequest
    ) -> RefreshAccepted:
        origin = request.headers.get("origin")
        allowed = {
            f"http://{host}:{port}"
            for host in ("localhost", "127.0.0.1")
            for port in (5173, 8787)
        }
        if origin is not None and origin not in allowed:
            raise HTTPException(403, "Origin is not allowed to start a refresh.")
        if (
            request.headers.get("content-type", "").split(";")[0].lower()
            != "application/json"
        ):
            raise HTTPException(415, "Explicit application/json is required.")
        if not enabled:
            raise HTTPException(503, "Manual refresh is disabled on this server.")
        try:
            run_id = service.start()
            response.headers["Cache-Control"] = "no-store"
            return RefreshAccepted(run_id=run_id)
        except RefreshConflict as error:
            raise HTTPException(
                error.status_code,
                str(error),
                headers={"Retry-After": str(error.retry_after)},
            ) from None
        except Exception:
            raise HTTPException(
                500, "Could not start refresh; diagnostics suppressed."
            ) from None

    @app.get(
        "/api/refresh/events",
        response_class=StreamingResponse,
        operation_id="streamRefresh",
        responses={
            200: {
                "description": (
                    "SSE events named refresh; data is RefreshSnapshot JSON. "
                    "Reconnect sends current state and up to 200 sanitized logs. "
                    "Connecting never starts a refresh."
                ),
                "content": {"text/event-stream": {"schema": {"type": "string"}}},
                "x-event-payload": {"$ref": "#/components/schemas/RefreshSnapshot"},
            }
        },
    )
    async def events(request: Request, once: bool = False) -> StreamingResponse:
        async def stream() -> AsyncIterator[str]:
            previous = ""
            while not await request.is_disconnected():
                payload = service.snapshot().model_dump_json()
                if payload != previous:
                    yield "event: refresh\ndata: " + payload + "\n\n"
                    previous = payload
                else:
                    yield ": keep-alive\n\n"
                if once:
                    break
                await asyncio.sleep(1)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    return app
