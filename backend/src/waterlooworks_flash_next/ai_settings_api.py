"""Admin-only AI settings routes. Saving never calls a model or switches extraction.

Every response is no-store and secret-free: credentials are reported only as
configured/expiry metadata. Provider checks (custom /models, OAuth endpoints)
run only on the explicit admin actions that name them.
"""

import asyncio
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from waterlooworks_flash_next.ai_custom import (
    ConnectionTest,
    CustomModel,
    CustomServerError,
    check_connection,
    list_models,
)
from waterlooworks_flash_next.ai_oauth import LoginManager, LoginStatus, OAuthFlowError
from waterlooworks_flash_next.ai_settings import (
    ActivateRequest,
    AIConfig,
    AISettingsError,
    AISettingsStore,
    AISettingsView,
    CredentialSlot,
    CustomToken,
)
from waterlooworks_flash_next.api_models import RefreshRequest
from waterlooworks_flash_next.app_auth import require_admin
from waterlooworks_flash_next.credentials import CredentialError
from waterlooworks_flash_next.pi_bridge import SidecarError, catalog


class CallbackPaste(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    callback_url: str = Field(min_length=1, max_length=8192, repr=False)


class ModelOption(BaseModel):
    id: str
    name: str
    loaded: bool | None = None
    context_window: int | None = None
    supports_tools: bool | None = None
    efforts: list[str]
    default_effort: str | None = None
    reasoning_field: str | None = None
    disable_with: dict[str, str | bool | int | None] | None = None


class ModelList(BaseModel):
    models: list[ModelOption]


class LoginResponse(BaseModel):
    login: LoginStatus | None


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _custom_option(model: CustomModel) -> ModelOption:
    reasoning = model.reasoning
    return ModelOption(
        id=model.id,
        name=model.name,
        loaded=model.loaded,
        context_window=model.context_window,
        supports_tools=model.supports_tools,
        efforts=reasoning.efforts if reasoning else ["off"],
        default_effort=reasoning.default if reasoning else None,
        reasoning_field=reasoning.request_field if reasoning else None,
        disable_with=reasoning.disable_with if reasoning else None,
    )


def install_ai_settings(app: FastAPI, path: Path) -> None:
    store = AISettingsStore(path)
    app.state.ai_logins = LoginManager(store)
    app.state.ai_custom_resolver = None
    app.state.ai_custom_transport = None
    app.state.ai_catalog = catalog
    admin = [Depends(require_admin)]

    def logins(request: Request) -> LoginManager:
        manager: LoginManager = request.app.state.ai_logins
        return manager

    def custom_options(request: Request) -> dict[str, object]:
        options: dict[str, object] = {}
        if request.app.state.ai_custom_resolver is not None:
            options["resolver"] = request.app.state.ai_custom_resolver
        if request.app.state.ai_custom_transport is not None:
            options["transport"] = request.app.state.ai_custom_transport
        return options

    def view() -> AISettingsView:
        # Explicit fixed 503 (never a 500) when the backend key is unavailable.
        try:
            return store.view()
        except CredentialError:
            raise HTTPException(
                503,
                "AI settings unavailable; check the backend key.",
                headers={"Cache-Control": "no-store"},
            ) from None

    @app.get(
        "/api/admin/ai",
        dependencies=admin,
        response_model=AISettingsView,
        operation_id="getAISettings",
    )
    def get_settings(response: Response) -> AISettingsView:
        _no_store(response)
        return view()

    @app.put(
        "/api/admin/ai",
        dependencies=admin,
        response_model=AISettingsView,
        operation_id="saveAISettings",
    )
    def save_settings(body: AIConfig, response: Response) -> AISettingsView:
        _no_store(response)
        try:
            store.save_draft(body)
        except AISettingsError as error:
            raise HTTPException(422, str(error)) from None
        return view()

    @app.post(
        "/api/admin/ai/activate",
        dependencies=admin,
        response_model=AISettingsView,
        operation_id="activateAISettings",
    )
    def activate(
        request: Request, body: ActivateRequest, response: Response
    ) -> AISettingsView:
        _no_store(response)
        try:
            store.activate(request.app.state.ai_custom_resolver)
        except AISettingsError as error:
            raise HTTPException(409, str(error)) from None
        except CredentialError:
            raise HTTPException(
                409, "Settings are incomplete or the access mode is not connected."
            ) from None
        return view()

    @app.post(
        "/api/admin/ai/deactivate",
        dependencies=admin,
        response_model=AISettingsView,
        operation_id="deactivateAISettings",
    )
    def deactivate(body: ActivateRequest, response: Response) -> AISettingsView:
        _no_store(response)
        store.deactivate()
        return view()

    @app.get(
        "/api/admin/ai/models",
        dependencies=admin,
        response_model=ModelList,
        operation_id="listAIModels",
    )
    async def catalog_models(
        request: Request,
        response: Response,
        mode: Literal["openai_api_key", "chatgpt_signin", "codex_device"],
    ) -> ModelList:
        _no_store(response)
        provider: Literal["openai", "openai-codex"] = (
            "openai-codex" if mode == "codex_device" else "openai"
        )
        try:
            entries = await asyncio.to_thread(request.app.state.ai_catalog, provider)
        except SidecarError:
            raise HTTPException(503, "Model catalog is unavailable.") from None
        return ModelList(
            models=[
                ModelOption(
                    id=entry["id"],
                    name=entry["name"],
                    context_window=entry["context_window"] or None,
                    supports_tools=True,
                    efforts=entry["efforts"] or ["off"],
                )
                for entry in entries
            ]
        )

    def _custom_token() -> str | None:
        credential = store.load_slot("custom_token")
        key = credential.get("key") if credential else None
        return key if isinstance(key, str) else None

    def _custom_base() -> str:
        base = store.read()[0].custom_base_url
        if base is None:
            raise HTTPException(409, "Save a custom server base URL first.")
        return base

    @app.post(
        "/api/admin/ai/custom/test",
        dependencies=admin,
        response_model=ConnectionTest,
        operation_id="testCustomAIServer",
    )
    async def custom_test(
        request: Request, response: Response, body: RefreshRequest
    ) -> ConnectionTest:
        _no_store(response)
        base, token = _custom_base(), _custom_token()
        return await asyncio.to_thread(
            lambda: check_connection(base, token, **custom_options(request))  # type: ignore[arg-type]
        )

    @app.post(
        "/api/admin/ai/custom/models",
        dependencies=admin,
        response_model=ModelList,
        operation_id="listCustomAIModels",
    )
    async def custom_models(
        request: Request, response: Response, body: RefreshRequest
    ) -> ModelList:
        _no_store(response)
        base, token = _custom_base(), _custom_token()
        try:
            models = await asyncio.to_thread(
                lambda: list_models(base, token, **custom_options(request))  # type: ignore[arg-type]
            )
        except CustomServerError as error:
            raise HTTPException(
                502, f"Custom server check failed: {error.category}."
            ) from None
        return ModelList(models=[_custom_option(model) for model in models])

    @app.put(
        "/api/admin/ai/custom/token",
        dependencies=admin,
        response_model=AISettingsView,
        operation_id="saveCustomAIToken",
    )
    def save_token(body: CustomToken, response: Response) -> AISettingsView:
        _no_store(response)
        store.save_custom_token(body)
        return view()

    @app.delete(
        "/api/admin/ai/credentials/{slot}",
        dependencies=admin,
        response_model=AISettingsView,
        operation_id="disconnectAICredential",
    )
    def disconnect(
        slot: CredentialSlot, response: Response, body: RefreshRequest
    ) -> AISettingsView:
        _no_store(response)
        store.delete(slot)
        return view()

    def _uid(request: Request) -> str:
        return str(request.state.identity.uid)

    @app.post(
        "/api/admin/ai/chatgpt/start",
        dependencies=admin,
        response_model=LoginResponse,
        operation_id="startChatGPTSignIn",
    )
    def chatgpt_start(
        request: Request, response: Response, body: RefreshRequest
    ) -> LoginResponse:
        _no_store(response)
        return LoginResponse(login=logins(request).start_chatgpt(_uid(request)))

    @app.post(
        "/api/admin/ai/chatgpt/complete",
        dependencies=admin,
        response_model=LoginResponse,
        operation_id="completeChatGPTSignIn",
    )
    async def chatgpt_complete(
        request: Request, response: Response, body: CallbackPaste
    ) -> LoginResponse:
        _no_store(response)
        try:
            status = await asyncio.to_thread(
                logins(request).complete_chatgpt, _uid(request), body.callback_url
            )
        except OAuthFlowError as error:
            raise HTTPException(
                400, f"Sign-in was not completed: {error.category}."
            ) from None
        return LoginResponse(login=status)

    @app.post(
        "/api/admin/ai/codex/start",
        dependencies=admin,
        response_model=LoginResponse,
        operation_id="startCodexDeviceLogin",
    )
    async def codex_start(
        request: Request, response: Response, body: RefreshRequest
    ) -> LoginResponse:
        _no_store(response)
        try:
            status = await asyncio.to_thread(logins(request).start_codex, _uid(request))
        except OAuthFlowError as error:
            raise HTTPException(
                502, f"Device login unavailable: {error.category}."
            ) from None
        return LoginResponse(login=status)

    @app.get(
        "/api/admin/ai/login",
        dependencies=admin,
        response_model=LoginResponse,
        operation_id="getAILoginStatus",
    )
    def login_status(request: Request, response: Response) -> LoginResponse:
        _no_store(response)
        return LoginResponse(login=logins(request).status(_uid(request)))

    @app.post(
        "/api/admin/ai/login/cancel",
        dependencies=admin,
        response_model=LoginResponse,
        operation_id="cancelAILogin",
    )
    def login_cancel(
        request: Request, response: Response, body: RefreshRequest
    ) -> LoginResponse:
        _no_store(response)
        manager = logins(request)
        manager.cancel(_uid(request))
        return LoginResponse(login=manager.status(_uid(request)))
