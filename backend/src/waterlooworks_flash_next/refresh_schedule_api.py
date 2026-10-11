"""Admin-only scheduled refresh routes. Never starts a refresh itself.

Every response is no-store (the admin middleware also enforces it). Mutations
go through the shared Origin/JSON/X-WW-Request guards. Validation errors are
fixed strings that never echo the submitted value.
"""

from fastapi import Depends, FastAPI, HTTPException, Response

from waterlooworks_flash_next.api_models import (
    RefreshScheduleView,
    ScheduleTimeRequest,
    ScheduleToggleRequest,
)
from waterlooworks_flash_next.app_auth import require_admin
from waterlooworks_flash_next.refresh_schedule import (
    RefreshScheduler,
    ScheduleError,
    parse_time,
)

ROUTE = "/api/admin/refresh-schedule"


def install_refresh_schedule(app: FastAPI, scheduler: RefreshScheduler) -> None:
    admin = [Depends(require_admin)]
    store = scheduler.store

    def no_store(response: Response) -> None:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"

    def minute(body: ScheduleTimeRequest) -> int:
        try:
            return parse_time(body.time)
        except ScheduleError as error:
            raise HTTPException(422, str(error)) from None

    def changed() -> RefreshScheduleView:
        scheduler.publish()
        return scheduler.view()

    @app.get(
        ROUTE,
        dependencies=admin,
        response_model=RefreshScheduleView,
        operation_id="getRefreshSchedule",
    )
    def get_schedule(response: Response) -> RefreshScheduleView:
        no_store(response)
        return scheduler.view()

    @app.post(
        ROUTE,
        dependencies=admin,
        response_model=RefreshScheduleView,
        operation_id="addRefreshScheduleTime",
    )
    def add_time(body: ScheduleTimeRequest, response: Response) -> RefreshScheduleView:
        no_store(response)
        value = minute(body)
        try:
            store.add(value, scheduler.min_interval_minutes)
        except ScheduleError as error:
            raise HTTPException(422, str(error)) from None
        return changed()

    @app.delete(
        ROUTE,
        dependencies=admin,
        response_model=RefreshScheduleView,
        operation_id="removeRefreshScheduleTime",
    )
    def remove_time(
        body: ScheduleTimeRequest, response: Response
    ) -> RefreshScheduleView:
        no_store(response)
        if not store.remove(minute(body)):
            raise HTTPException(404, "That time is not scheduled.")
        return changed()

    @app.put(
        f"{ROUTE}/enabled",
        dependencies=admin,
        response_model=RefreshScheduleView,
        operation_id="setRefreshScheduleEnabled",
    )
    def set_enabled(
        body: ScheduleToggleRequest, response: Response
    ) -> RefreshScheduleView:
        no_store(response)
        store.set_enabled(body.enabled)
        return changed()
