"""Google-authenticated shared API; admin-only explicit collection."""

import asyncio
import json
import os
import sqlite3
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi import Path as ApiPath
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.middleware.gzip import GZipMiddleware

from waterlooworks_flash_next.analysis_read import (
    read_analysis,
    read_comparisons,
    read_history,
)
from waterlooworks_flash_next.api_models import (
    CountryInference,
    ErrorResponse,
    ExchangeRateResponse,
    HealthResponse,
    JobDetailResponse,
    KeywordIndexResponse,
    ListingRevision,
    PostingAnalysisResponse,
    PostingComparisonResponse,
    PostingHistoryResponse,
    PostingResponse,
    RefreshAccepted,
    RefreshRequest,
    RefreshSnapshot,
    WorkspaceResponse,
)
from waterlooworks_flash_next.app_auth import (
    COOKIE,
    AuthTransport,
    FirebaseVerifier,
    SessionStore,
    require_admin,
    require_session,
)
from waterlooworks_flash_next.auth_api import install_auth
from waterlooworks_flash_next.auth_challenge import AuthChallengeStatus
from waterlooworks_flash_next.compact_history import HistoryReader
from waterlooworks_flash_next.compensation_sort import read_compensation_sorts
from waterlooworks_flash_next.content import StoredPostingContent
from waterlooworks_flash_next.country_inference import read_country_inferences
from waterlooworks_flash_next.credentials import (
    AdminCredentialStatus,
    CredentialError,
    CredentialReplacement,
    CredentialStatus,
    CredentialStore,
)
from waterlooworks_flash_next.database import (
    SCHEMA_VERSION,
    StoredListing,
    open_database,
)
from waterlooworks_flash_next.exchange_rates import ExchangeRates
from waterlooworks_flash_next.keyword_index import read_keyword_index
from waterlooworks_flash_next.openai_settings_api import install_openai_settings
from waterlooworks_flash_next.openai_usage_api import install_openai_usage
from waterlooworks_flash_next.refresh import (
    RefreshConflict,
    RefreshService,
    configured_detail_limit,
)
from waterlooworks_flash_next.snapshot_cache import SnapshotCache
from waterlooworks_flash_next.targeted_disciplines import read_targeted_disciplines
from waterlooworks_flash_next.work_arrangement import read_work_arrangements


def create_app(database_path: Path | None = None) -> FastAPI:
    path = database_path or Path(
        os.environ.get("WW_DATABASE", "data/waterlooworks.sqlite3")
    )
    # Explicit database paths never enable collection through environment inheritance.
    # Google authentication remains mandatory regardless of this collection setting.
    enabled = database_path is None and os.environ.get("WW_ENABLE_REFRESH") == "1"
    root = Path(os.environ.get("WW_PROJECT_ROOT", str(Path.cwd())))
    detail_limit = (
        configured_detail_limit(os.environ.get("WW_DETAIL_LIMIT"))
        if database_path is None
        else None
    )
    service = RefreshService(path, root if enabled else None, detail_limit=detail_limit)

    # Interactive docs load third-party CDN script on the app origin and expose
    # the API surface unauthenticated, so they are opt-in for local work only.
    docs = os.environ.get("WW_API_DOCS") == "1"
    verifier = FirebaseVerifier(root if database_path is None else path.parent)
    sessions = SessionStore(path)
    workspace_cache = SnapshotCache[bytes](path)
    keyword_cache = SnapshotCache[tuple[bytes, str]](path)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        verifier.load_environment()
        app.state.transport = AuthTransport.from_environment()
        open_database(path).close()
        service.restore()
        try:
            yield
        finally:
            verifier.close()
            workspace_cache.close()
            keyword_cache.close()

    app = FastAPI(
        title="WaterlooWorks Flash Next local API",
        version="0.1.0",
        description=(
            "Google-authenticated shared listings API. Admin-only refresh with "
            "a 300-second post-run cooldown and sequential detail content "
            "after the full listing save. "
            "Configure safe HTTPS and the pinned owner bootstrap before opening access."
        ),
        responses={
            401: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
        },
        docs_url="/doc" if docs else None,
        openapi_url="/doc/openapi.json" if docs else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    # Starlette excludes text/event-stream so SSE remains promptly flushed.
    app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=4)
    errors: dict[int | str, dict[str, Any]] = {
        code: {"model": ErrorResponse} for code in (403, 409, 415, 422, 429, 500, 503)
    }

    install_auth(app, verifier, sessions)
    install_openai_settings(app, path, verifier)
    install_openai_usage(app, path)
    protected = APIRouter(dependencies=[Depends(require_session)])

    @app.exception_handler(RequestValidationError)
    async def invalid_request(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"detail": "Invalid request; input diagnostics suppressed."},
        )

    @app.get(
        "/api/admin/refresh-auth",
        dependencies=[Depends(require_admin)],
        response_model=AuthChallengeStatus,
        operation_id="getRefreshAuth",
    )
    def refresh_auth(request: Request, response: Response) -> AuthChallengeStatus:
        response.headers["Cache-Control"] = "no-store"
        # Recheck the actual current cookie/admin after dependency admission.
        identity = sessions.lookup(request.cookies.get(COOKIE))
        if identity is None:
            raise HTTPException(401, "Sign in to continue.")
        if identity.role != "admin" or identity.uid != request.state.identity.uid:
            raise HTTPException(403, "Administrator access required.")
        return service.challenge_status()

    credential_store = CredentialStore(path)

    @app.get(
        "/api/admin/waterlooworks-credentials",
        dependencies=[Depends(require_admin)],
        response_model=AdminCredentialStatus,
        operation_id="getWaterlooWorksCredentialsStatus",
    )
    def credentials_status() -> AdminCredentialStatus:
        try:
            return credential_store.admin_status()
        except CredentialError:
            raise HTTPException(
                503,
                "WaterlooWorks credential settings unavailable; check the backend key.",
            ) from None

    @app.post(
        "/api/admin/waterlooworks-credentials",
        dependencies=[Depends(require_admin)],
        response_model=CredentialStatus,
        operation_id="saveWaterlooWorksCredentials",
    )
    def save_credentials(body: CredentialReplacement) -> CredentialStatus:
        try:
            return credential_store.save(body)
        except CredentialError:
            raise HTTPException(
                503,
                "WaterlooWorks credential settings unavailable; check the backend key.",
            ) from None

    exchange_rates = ExchangeRates()

    @protected.get(
        "/api/exchange-rates",
        response_model=ExchangeRateResponse,
        operation_id="getExchangeRates",
    )
    def get_exchange_rates() -> ExchangeRateResponse:
        try:
            return ExchangeRateResponse.model_validate(exchange_rates.read())
        except Exception:
            raise HTTPException(
                503, "Reference exchange rates are unavailable."
            ) from None

    @app.get("/api/health", response_model=HealthResponse, operation_id="getHealth")
    def health() -> HealthResponse:
        return HealthResponse(schema_version=SCHEMA_VERSION)

    @protected.get(
        "/api/workspace",
        response_model=WorkspaceResponse,
        responses=errors,
        operation_id="getWorkspace",
    )
    def workspace(request: Request) -> Response:
        try:
            # Cache only identity-free data; attach authorization outside the cache.
            payload = json.loads(
                workspace_cache.get(
                    lambda: workspace_snapshot().model_dump_json().encode()
                )
            )
            payload["manual_refresh_available"] = (
                enabled and request.state.identity.role == "admin"
            )
            return Response(
                json.dumps(payload),
                media_type="application/json",
                headers={"Cache-Control": "no-store"},
            )
        except Exception:
            raise HTTPException(
                500, "Stored workspace is unavailable; diagnostics suppressed."
            ) from None

    def workspace_snapshot() -> WorkspaceResponse:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute(
                "BEGIN"
            )  # One source/projection snapshot during refresh.
            arrangements = read_work_arrangements(connection)
            targeted = read_targeted_disciplines(connection)
            compensation = read_compensation_sorts(connection)
            countries = read_country_inferences(connection)
            history: dict[str, list[ListingRevision]] = {}
            history_reader = HistoryReader(connection)
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
                        snapshot=StoredListing.model_validate_json(
                            history_reader.read(
                                row["payload_id"], row["content_hash"], "listing"
                            )
                        ),
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
                        targeted_disciplines=targeted.get(row["posting_id"], []),
                        work_arrangement=arrangements.get(row["posting_id"]),
                        compensation_sort=compensation.get(row["posting_id"]),
                        country_inference=countries.get(
                            row["posting_id"], CountryInference()
                        ),
                    )
                )
            return WorkspaceResponse(postings=postings, manual_refresh_available=False)
        except Exception:
            raise HTTPException(
                500, "Stored workspace is unavailable; diagnostics suppressed."
            ) from None
        finally:
            connection.close()

    @protected.get(
        "/api/postings/{posting_id}",
        response_model=JobDetailResponse,
        responses={404: {"model": ErrorResponse}},
        operation_id="getPosting",
    )
    def posting_detail(
        posting_id: Annotated[str, ApiPath(pattern=r"^[0-9]+$")],
        response: Response,
    ) -> JobDetailResponse:
        """Read the stored snapshot only; never fetch content or expose attempts."""
        response.headers["Cache-Control"] = "no-store"
        connection = None
        try:
            connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN")
            countries = read_country_inferences(connection, posting_id)
            row = connection.execute(
                "SELECT p.listing_json, c.content_json, c.last_seen_at "
                "FROM postings p LEFT JOIN posting_contents c "
                "ON c.posting_id=p.posting_id WHERE p.posting_id=?",
                (posting_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(
                    404,
                    "Stored posting not found.",
                    headers={"Cache-Control": "no-store"},
                )
            return JobDetailResponse(
                posting_id=posting_id,
                listing=StoredListing.model_validate_json(row["listing_json"]),
                content=StoredPostingContent.model_validate_json(row["content_json"])
                if row["content_json"] is not None
                else None,
                content_observed_at=row["last_seen_at"],
                country_inference=countries.get(posting_id, CountryInference()),
            )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                500,
                "Stored posting is unavailable; diagnostics suppressed.",
                headers={"Cache-Control": "no-store"},
            ) from None
        finally:
            if connection is not None:
                connection.close()

    AnalysisResponse = (
        PostingAnalysisResponse | PostingHistoryResponse | PostingComparisonResponse
    )

    def read_projection_endpoint(
        posting_id: str,
        reader: Callable[[sqlite3.Connection, str], AnalysisResponse],
    ) -> AnalysisResponse:
        connection = None
        try:
            connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN")  # Keep source and projection reads consistent.
            return reader(connection, posting_id)
        except KeyError:
            raise HTTPException(
                404,
                "Stored posting not found.",
                headers={"Cache-Control": "no-store"},
            ) from None
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                500,
                "Stored posting analysis is unavailable; diagnostics suppressed.",
                headers={"Cache-Control": "no-store"},
            ) from None
        finally:
            if connection is not None:
                connection.close()

    @protected.get(
        "/api/postings/{posting_id}/analysis",
        response_model=PostingAnalysisResponse,
        responses={404: {"model": ErrorResponse}},
        operation_id="getPostingAnalysis",
    )
    def posting_analysis(
        posting_id: Annotated[str, ApiPath(pattern=r"^[0-9]+$")],
        response: Response,
    ) -> PostingAnalysisResponse:
        response.headers["Cache-Control"] = "no-store"
        return read_projection_endpoint(posting_id, read_analysis)  # type: ignore[return-value]

    @protected.get(
        "/api/postings/{posting_id}/history",
        response_model=PostingHistoryResponse,
        responses={404: {"model": ErrorResponse}},
        operation_id="getPostingHistory",
    )
    def posting_history(
        posting_id: Annotated[str, ApiPath(pattern=r"^[0-9]+$")],
        response: Response,
    ) -> PostingHistoryResponse:
        response.headers["Cache-Control"] = "no-store"
        return read_projection_endpoint(posting_id, read_history)  # type: ignore[return-value]

    @protected.get(
        "/api/postings/{posting_id}/comparisons",
        response_model=PostingComparisonResponse,
        responses={404: {"model": ErrorResponse}},
        operation_id="getPostingComparisons",
    )
    def posting_comparisons(
        posting_id: Annotated[str, ApiPath(pattern=r"^[0-9]+$")],
        response: Response,
    ) -> PostingComparisonResponse:
        response.headers["Cache-Control"] = "no-store"
        return read_projection_endpoint(posting_id, read_comparisons)  # type: ignore[return-value]

    @protected.get(
        "/api/keyword-index",
        response_model=KeywordIndexResponse,
        responses={304: {"description": "Saved keyword index unchanged"}},
        operation_id="getKeywordIndex",
    )
    def keyword_index(request: Request) -> Response:
        try:
            content, etag = keyword_cache.get(keyword_snapshot)
            headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
            if request.headers.get("if-none-match") == etag:
                return Response(status_code=304, headers=headers)
            return Response(content, media_type="application/json", headers=headers)
        except Exception:
            raise HTTPException(
                500, "Saved keyword index unavailable; diagnostics suppressed."
            ) from None

    def keyword_snapshot() -> tuple[bytes, str]:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            payload, etag = read_keyword_index(connection)
            assert payload is not None
            return payload.model_dump_json().encode(), etag
        finally:
            connection.close()

    @protected.get(
        "/api/refresh", response_model=RefreshSnapshot, operation_id="getRefresh"
    )
    def refresh_status(response: Response) -> RefreshSnapshot:
        response.headers["Cache-Control"] = "no-store"
        return service.snapshot()

    @protected.post(
        "/api/refresh",
        dependencies=[Depends(require_admin)],
        status_code=202,
        response_model=RefreshAccepted,
        responses=errors,
        operation_id="startRefresh",
    )
    def start_refresh(
        request: Request, response: Response, body: RefreshRequest
    ) -> RefreshAccepted:
        if not enabled:
            raise HTTPException(503, "Manual refresh is disabled on this server.")
        try:
            run_id = service.start()
            response.headers["Cache-Control"] = "no-store"
            return RefreshAccepted(run_id=run_id)
        except CredentialError:
            raise HTTPException(
                503,
                "WaterlooWorks credentials unavailable; configure admin settings "
                "and the backend key before refreshing.",
            ) from None
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

    @protected.get(
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
        return StreamingResponse(
            session_event_stream(request, service, sessions, once=once),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    app.include_router(protected)
    return app


async def session_event_stream(
    request: Request,
    service: RefreshService,
    sessions: SessionStore,
    *,
    once: bool = False,
) -> AsyncIterator[str]:
    """Recheck the persisted session before every SSE emission, not only connection."""
    previous = ""
    while not await request.is_disconnected():
        if sessions.lookup(request.cookies.get(COOKIE)) is None:
            break
        payload = service.snapshot().model_dump_json()
        if payload != previous:
            yield "event: refresh\ndata: " + payload + "\n\n"
            previous = payload
        else:
            yield ": keep-alive\n\n"
        if once:
            break
        await asyncio.sleep(1)
