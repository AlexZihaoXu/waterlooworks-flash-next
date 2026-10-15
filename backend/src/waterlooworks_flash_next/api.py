"""Google-authenticated shared API; admin-only explicit collection."""

import asyncio
import contextlib
import os
import sqlite3
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi import Path as ApiPath
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask
from starlette.middleware.gzip import GZipMiddleware

from waterlooworks_flash_next.account_api import install_account
from waterlooworks_flash_next.agent_api import install_agent_api
from waterlooworks_flash_next.ai_settings_api import install_ai_settings
from waterlooworks_flash_next.analysis_read import (
    read_analysis,
    read_comparisons,
    read_history,
)
from waterlooworks_flash_next.api_models import (
    AdminRefreshStatus,
    CountryInference,
    ErrorResponse,
    ExchangeRateResponse,
    HealthResponse,
    JobDetailResponse,
    KeywordIndexResponse,
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
from waterlooworks_flash_next.compensation_sort import read_compensation_sorts
from waterlooworks_flash_next.content import StoredPostingContent
from waterlooworks_flash_next.content_storage import (
    FORMATTING_JOIN,
    formatting_from_row,
)
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
from waterlooworks_flash_next.discord_alerts import (
    DiscordAlerts,
    configured_public_base_url,
)
from waterlooworks_flash_next.discord_api import install_discord_webhooks
from waterlooworks_flash_next.event_streams import (
    StreamLease,
    StreamLimiter,
    session_key,
)
from waterlooworks_flash_next.exchange_rates import ExchangeRates
from waterlooworks_flash_next.incidents import suppressed_failure
from waterlooworks_flash_next.keyword_index import read_keyword_index
from waterlooworks_flash_next.openai_settings_api import install_openai_settings
from waterlooworks_flash_next.openai_usage_api import install_openai_usage
from waterlooworks_flash_next.refresh import (
    RECENT_HEADER,
    RefreshConflict,
    RefreshService,
    RunOutcome,
    configured_detail_limit,
    configured_format_backfill,
    configured_full_refetch,
)
from waterlooworks_flash_next.refresh_schedule import RefreshScheduler, ScheduleStore
from waterlooworks_flash_next.refresh_schedule_api import install_refresh_schedule
from waterlooworks_flash_next.rich_text import FORMAT_VERSION
from waterlooworks_flash_next.shutdown import ShutdownNotice
from waterlooworks_flash_next.snapshot_cache import SnapshotCache
from waterlooworks_flash_next.targeted_disciplines import read_targeted_disciplines
from waterlooworks_flash_next.work_arrangement import read_work_arrangements
from waterlooworks_flash_next.ww_pacing import PacingConfig


def create_app(database_path: Path | None = None) -> FastAPI:
    path = database_path or Path(
        os.environ.get("WW_DATABASE", "data/waterlooworks.sqlite3")
    )
    # Explicit database paths never enable collection through environment inheritance.
    # Google authentication remains mandatory regardless of this collection setting.
    enabled = database_path is None and os.environ.get("WW_ENABLE_REFRESH") == "1"
    # Automatic scheduled refreshes need a second, explicit server gate.
    scheduling = enabled and os.environ.get("WW_ENABLE_SCHEDULE") == "1"
    root = Path(os.environ.get("WW_PROJECT_ROOT", str(Path.cwd())))
    detail_limit = (
        configured_detail_limit(os.environ.get("WW_DETAIL_LIMIT"))
        if database_path is None
        else None
    )
    full_refetch = (
        configured_full_refetch(os.environ.get("WW_DETAIL_FULL_REFETCH"))
        if database_path is None
        else False
    )
    format_backfill = configured_format_backfill(
        os.environ.get("WW_FORMAT_BACKFILL_PER_RUN") if database_path is None else None
    )
    # Fails closed at startup with a fixed message on an invalid value.
    pacing = (
        PacingConfig.from_environment() if database_path is None else PacingConfig()
    )
    service = RefreshService(
        path,
        root if enabled else None,
        detail_limit=detail_limit,
        full_refetch=full_refetch,
        pacing=pacing,
        format_backfill=format_backfill,
    )
    scheduler = RefreshScheduler(
        ScheduleStore(path),
        service,
        active=scheduling,
        min_interval_minutes=pacing.min_interval_minutes,
    )
    # Links and the avatar in Discord alerts; unset omits them (never guessed).
    public_base_url = (
        configured_public_base_url(os.environ.get("WW_PUBLIC_BASE_URL"))
        if database_path is None
        else None
    )

    # Interactive docs load third-party CDN script on the app origin and expose
    # the API surface unauthenticated, so they are opt-in for local work only.
    docs = os.environ.get("WW_API_DOCS") == "1"
    verifier = FirebaseVerifier(root if database_path is None else path.parent)
    sessions = SessionStore(path)
    workspace_cache = SnapshotCache[tuple[WorkspaceResponse, bytes, bytes]](path)
    keyword_cache = SnapshotCache[tuple[bytes, str]](path)
    event_streams = StreamLimiter()
    shutdown = ShutdownNotice()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        verifier.load_environment()
        app.state.transport = AuthTransport.from_environment()
        open_database(path).close()
        service.restore()
        scheduler.publish()
        app.state.refresh_scheduler_task = (
            asyncio.create_task(scheduler.run(), name="refresh-scheduler")
            if scheduling
            else None
        )
        # uvicorn runs the lifespan shutdown only after open responses finish, and
        # SSE streams never finish on their own: flag the worker and end streams as
        # soon as the signal arrives instead.
        uninstall = shutdown.install(lambda: service.request_stop(timeout=0))
        try:
            yield
        finally:
            uninstall()
            task = app.state.refresh_scheduler_task
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            # Cooperative stop: the worker finalizes its run as interrupted at the
            # next page/posting/analysis-batch checkpoint before SIGKILL.
            await asyncio.to_thread(service.request_stop)
            verifier.close()
            workspace_cache.close()
            keyword_cache.close()

    app = FastAPI(
        title="WaterlooWorks Flash Next local API",
        version="0.1.0",
        description=(
            "Google-authenticated shared listings API. Admin-only refresh with "
            "durable WaterlooWorks pacing: within the minimum interval after a "
            "run finishes a manual refresh needs explicit confirmation "
            "(confirm_recent), while blocks and the rolling budget always "
            "refuse. Sequential detail content follows the full listing save. "
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
    install_ai_settings(app, path)
    install_refresh_schedule(app, scheduler)
    install_account(app, path)
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
        response_model=AdminRefreshStatus,
        operation_id="getRefreshAuth",
    )
    def refresh_auth(request: Request, response: Response) -> AdminRefreshStatus:
        response.headers["Cache-Control"] = "no-store"
        # Recheck the actual current cookie/admin after dependency admission.
        identity = sessions.lookup(request.cookies.get(COOKIE))
        if identity is None:
            raise HTTPException(401, "Sign in to continue.")
        if identity.role != "admin" or identity.uid != request.state.identity.uid:
            raise HTTPException(403, "Administrator access required.")
        return service.admin_status()

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
            # Cache identity-free bytes for both capability variants; choose per
            # request so neither role nor UID enters the shared cache.
            _, member, admin = workspace_cache.get(workspace_variants)
            return Response(
                admin if enabled and request.state.identity.role == "admin" else member,
                media_type="application/json",
                headers={"Cache-Control": "no-store"},
            )
        except HTTPException:
            raise  # workspace_snapshot already recorded its incident.
        except Exception:
            raise suppressed_failure(
                "Stored workspace is unavailable; diagnostics suppressed."
            ) from None

    def workspace_variants() -> tuple[WorkspaceResponse, bytes, bytes]:
        snapshot = workspace_snapshot()
        return (
            snapshot,
            snapshot.model_dump_json().encode(),
            snapshot.model_copy(update={"manual_refresh_available": True})
            .model_dump_json()
            .encode(),
        )

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
            raise suppressed_failure(
                "Stored workspace is unavailable; diagnostics suppressed."
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
                "SELECT p.listing_json, c.content_json, c.last_seen_at, "
                "f.blocks_json FROM postings p LEFT JOIN posting_contents c "
                f"ON c.posting_id=p.posting_id {FORMATTING_JOIN} "
                "WHERE p.posting_id=?",
                (FORMAT_VERSION, posting_id),
            ).fetchone()
            if row is None:
                raise HTTPException(
                    404,
                    "Stored posting not found.",
                    headers={"Cache-Control": "no-store"},
                )
            content = (
                StoredPostingContent.model_validate_json(row["content_json"])
                if row["content_json"] is not None
                else None
            )
            return JobDetailResponse(
                posting_id=posting_id,
                listing=StoredListing.model_validate_json(row["listing_json"]),
                content=content,
                content_observed_at=row["last_seen_at"],
                formatting=formatting_from_row(content, row["blocks_json"])
                if content is not None
                else None,
                country_inference=countries.get(posting_id, CountryInference()),
            )
        except HTTPException:
            raise
        except Exception:
            raise suppressed_failure(
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
            raise suppressed_failure(
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
            raise suppressed_failure(
                "Saved keyword index unavailable; diagnostics suppressed."
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

    refresh_errors: dict[int | str, dict[str, Any]] = {
        **errors,
        409: {
            "model": ErrorResponse,
            "description": (
                "A refresh is already running, or (with the "
                f"{RECENT_HEADER} header) the previous run finished within the "
                "minimum interval: resend with confirm_recent=true after "
                "asking the admin. Scheduled refreshes never confirm."
            ),
            "headers": {
                RECENT_HEADER: {
                    "description": (
                        "Present only on the confirmable refusal: the previous "
                        "run's finish time (UTC ISO 8601)."
                    ),
                    "schema": {"type": "string"},
                }
            },
        },
        429: {
            "model": ErrorResponse,
            "description": (
                "Ban-risk/rate-limit block or rolling 24-hour request budget; "
                "never overridable by confirm_recent."
            ),
            "headers": {
                "Retry-After": {
                    "description": "Whole seconds until the hard wait ends.",
                    "schema": {"type": "integer"},
                }
            },
        },
    }

    @protected.post(
        "/api/refresh",
        dependencies=[Depends(require_admin)],
        status_code=202,
        response_model=RefreshAccepted,
        responses=refresh_errors,
        operation_id="startRefresh",
    )
    def start_refresh(
        request: Request, response: Response, body: RefreshRequest
    ) -> RefreshAccepted:
        if not enabled:
            raise HTTPException(503, "Manual refresh is disabled on this server.")
        try:
            run_id = service.start(confirm_recent=body.confirm_recent)
            response.headers["Cache-Control"] = "no-store"
            return RefreshAccepted(run_id=run_id)
        except CredentialError:
            raise HTTPException(
                503,
                "WaterlooWorks credentials unavailable; configure admin settings "
                "and the backend key before refreshing.",
            ) from None
        except RefreshConflict as error:
            if error.recent_finished_at is not None:
                raise HTTPException(
                    409,
                    str(error),
                    headers={
                        RECENT_HEADER: error.recent_finished_at,
                        "Cache-Control": "no-store",
                    },
                ) from None
            raise HTTPException(
                error.status_code,
                str(error),
                headers={"Retry-After": str(error.retry_after)},
            ) from None
        except Exception:
            raise suppressed_failure(
                "Could not start refresh; diagnostics suppressed."
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
            },
            429: {
                "model": ErrorResponse,
                "description": "Per-session or server-wide live stream cap reached.",
            },
        },
    )
    async def events(request: Request, once: bool = False) -> StreamingResponse:
        lease = event_streams.acquire(session_key(request.cookies.get(COOKIE)))
        if lease is None:
            raise HTTPException(
                429,
                "Too many live update streams are open; close another tab and retry.",
                headers={"Retry-After": "5", "Cache-Control": "no-store"},
            )
        return StreamingResponse(
            session_event_stream(
                request,
                service,
                sessions,
                once=once,
                lease=lease,
                shutdown=shutdown,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
            # Idempotent backstop for a response whose body never starts.
            background=BackgroundTask(lease.release),
        )

    def member_postings() -> list[PostingResponse]:
        # The identity-free member listing; marks are overlaid per caller.
        return workspace_cache.get(workspace_variants)[0].postings

    def reference_rates() -> dict[str, str] | None:
        rates = exchange_rates.read().get("cad_per_unit")
        return (
            {str(key): str(value) for key, value in rates.items()}
            if isinstance(rates, dict)
            else None
        )

    # New-job alerts use the member-visible workspace and run off the refresh
    # worker after each run is finalized; the agent API has no trigger.
    alerts = DiscordAlerts(
        path, member_postings, base_url=public_base_url, fx=reference_rates
    )
    app.state.discord_alerts = alerts
    install_discord_webhooks(app, alerts)

    def run_finished(outcome: RunOutcome) -> None:
        try:
            # Scheduled runs that fail sign-in or hit a ban-risk signal turn it off.
            scheduler.run_finished(outcome)
        finally:
            alerts.run_finished(outcome)

    service.on_finished = run_finished

    install_agent_api(
        app,
        path=path,
        postings=member_postings,
        service=service,
        verifier=verifier,
        guide=root / "docs" / "agent-api.md",
    )
    app.include_router(protected)
    app.state.event_streams = event_streams
    return app


async def session_event_stream(
    request: Request,
    service: RefreshService,
    sessions: SessionStore,
    *,
    once: bool = False,
    lease: StreamLease | None = None,
    shutdown: ShutdownNotice | None = None,
) -> AsyncGenerator[str, None]:
    """Recheck the persisted session before every SSE emission, not only connection.

    SQLite session reads and the refresh-state lock run in worker threads so a
    slow lock holder never stalls the event loop for every other request.
    """
    cookie = request.cookies.get(COOKIE)
    previous = ""
    # Session renewal rotates the cookie and ends this stream within the grace
    # window; a 1-second retry makes EventSource reconnect with the new cookie.
    prefix = "retry: 1000\n"
    try:
        while not await request.is_disconnected():
            if shutdown is not None and shutdown.is_set():
                break
            if await asyncio.to_thread(sessions.lookup, cookie) is None:
                break
            payload = await asyncio.to_thread(_snapshot_json, service)
            if payload != previous:
                yield prefix + "event: refresh\ndata: " + payload + "\n\n"
                previous, prefix = payload, ""
            else:
                yield ": keep-alive\n\n"
            if once:
                break
            await asyncio.sleep(1)
    finally:
        if lease is not None:
            lease.release()


def _snapshot_json(service: RefreshService) -> str:
    return service.snapshot().model_dump_json()
