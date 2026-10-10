"""Fixed Responses transport for explicit inference; never logs payloads."""

import asyncio
import concurrent.futures
import json
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from waterlooworks_flash_next.credentials import CredentialError
from waterlooworks_flash_next.openai_ledger import AttemptLedger
from waterlooworks_flash_next.openai_settings import OpenAISettingsStore
from waterlooworks_flash_next.openai_usage import MODEL, UsageCounters

ENDPOINT = "https://api.openai.com/v1/responses"
TEMPLATE_VERSION = "openai-responses-low-strict-v1"
MAX_BODY_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 120
MAX_OUTPUT_TOKENS = 4096
# Bounded retries for the ledger write after a dispatch; persistent failure keeps
# the attempt unfinalized (unknown usage, never assumed free).
FINALIZE_RETRY_DELAYS: tuple[float, ...] = (0.25, 1.0)
# Rejected-request statuses that are worth continuing past at known zero cost.
TRANSIENT_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
_slots = threading.BoundedSemaphore(4)
_active_lock = threading.Lock()
_active_attempts: set[str] = set()


class ProviderError(Exception):
    def __init__(self, category: str, *, transient: bool = False) -> None:
        self.category = category
        # Only a known-zero-cost rejection that later requests may not repeat.
        self.transient = transient
        super().__init__(category)


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Responses strict mode requires every key, including nullable ones."""

    def convert(value: Any) -> Any:
        if isinstance(value, list):
            return [convert(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {
            key: convert(item)
            for key, item in value.items()
            if key not in ("default", "title")
        }
        if result.get("type") == "object":
            result["additionalProperties"] = False
            result["required"] = list(result.get("properties", {}))
        return result

    return dict(convert(schema))


def build_request(
    messages: list[dict[str, str]],
    name: str,
    schema: dict[str, Any],
    max_output_tokens: int,
) -> dict[str, Any]:
    if (
        not re.fullmatch(r"[a-z_]{1,64}", name)
        or type(max_output_tokens) is not int
        or not 1 <= max_output_tokens <= MAX_OUTPUT_TOKENS
    ):
        raise ProviderError("configuration")
    if any(
        set(message) != {"role", "content"}
        or message["role"] not in ("system", "user")
        or not isinstance(message["content"], str)
        for message in messages
    ):
        raise ProviderError("configuration")
    return {
        "model": MODEL,
        "input": messages,
        "reasoning": {"effort": "low"},
        "service_tier": "default",
        "store": False,
        "max_output_tokens": max_output_tokens,
        "text": {
            "format": {
                "type": "json_schema",
                "name": name,
                "strict": True,
                "schema": strict_schema(schema),
            }
        },
    }


def usage_from_response(payload: dict[str, Any]) -> UsageCounters:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return UsageCounters()
    inputs = usage.get("input_tokens_details")
    outputs = usage.get("output_tokens_details")
    inputs = inputs if isinstance(inputs, dict) else {}
    outputs = outputs if isinstance(outputs, dict) else {}
    try:
        return UsageCounters(
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            cached_tokens=inputs.get("cached_tokens"),
            cache_write_tokens=inputs.get("cache_write_tokens"),
            reasoning_tokens=outputs.get("reasoning_tokens"),
        )
    except ValidationError:
        return UsageCounters()


def response_text(payload: dict[str, Any]) -> str:
    if not payload:
        raise ProviderError("response_schema")
    if payload.get("model") != MODEL:
        raise ProviderError("model_mismatch")
    if payload.get("status") == "incomplete":
        raise ProviderError("incomplete")
    if payload.get("error") is not None:
        raise ProviderError("provider_error")
    output = payload.get("output")
    if not isinstance(output, list):
        raise ProviderError("response_schema")
    texts = []
    for item in output:
        if not isinstance(item, dict):
            raise ProviderError("response_schema")
        if item.get("type") == "reasoning":
            continue
        if (
            item.get("type") != "message"
            or item.get("role") != "assistant"
            or not isinstance(item.get("content"), list)
        ):
            raise ProviderError("response_schema")
        for part in item["content"]:
            if not isinstance(part, dict):
                raise ProviderError("response_schema")
            if part.get("type") == "refusal":
                raise ProviderError("refusal")
            if part.get("type") != "output_text" or not isinstance(
                part.get("text"), str
            ):
                raise ProviderError("response_schema")
            texts.append(part["text"])
    if payload.get("status") != "completed" or len(texts) != 1:
        raise ProviderError("response_schema")
    return str(texts[0])


def is_error_envelope(status: int, payload: dict[str, Any]) -> bool:
    """A non-2xx OpenAI error body without usage is a known zero-cost rejection."""
    return (
        not 200 <= status < 300
        and isinstance(payload.get("error"), dict)
        and "usage" not in payload
    )


def _transient(status: int, payload: dict[str, Any]) -> bool:
    error = payload.get("error")
    exhausted = isinstance(error, dict) and "insufficient_quota" in (
        error.get("code"),
        error.get("type"),
    )
    return status in TRANSIENT_STATUSES and not exhausted


def _record(write: Callable[[], None]) -> None:
    for delay in (*FINALIZE_RETRY_DELAYS, None):
        try:
            write()
            return
        except Exception:
            if delay is None:
                raise
            time.sleep(delay)


def _metadata(value: object) -> str | None:
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_.:-]{1,100}", value)
        else None
    )


async def _dispatch(
    client: httpx.AsyncClient, body: bytes, key: str
) -> tuple[int, dict[str, Any]]:
    try:
        async with asyncio.timeout(TIMEOUT_SECONDS):
            async with client.stream(
                "POST",
                ENDPOINT,
                content=body,
                headers={
                    "Authorization": "Bearer " + key,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                },
            ) as response:
                if response.headers.get("Content-Encoding", "identity") != "identity":
                    raise ProviderError("response_schema")
                declared = response.headers.get("Content-Length")
                if declared is not None and (
                    not declared.isdigit() or int(declared) > MAX_RESPONSE_BYTES
                ):
                    raise ProviderError("response_size")
                raw = bytearray()
                async for chunk in response.aiter_raw(chunk_size=65536):
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise ProviderError("response_size")
                try:
                    payload = json.loads(
                        raw,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
                    )
                except (ValueError, UnicodeError):
                    raise ProviderError("response_schema") from None
                if not isinstance(payload, dict):
                    raise ProviderError("response_schema")
                return response.status_code, payload
    except ProviderError:
        raise
    except (TimeoutError, httpx.TimeoutException):
        raise ProviderError("timeout") from None
    except Exception:
        raise ProviderError("connection") from None


class _Transport:
    """One event-loop thread owning one pooled AsyncClient for a provider.

    Calls from worker threads share TCP/TLS connections while each dispatch keeps
    its own 120-second total deadline. No proxies, redirects or retries.
    """

    def __init__(self) -> None:
        self.client = httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
            timeout=30,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
        )
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(
            target=self.loop.run_forever, name="openai-responses", daemon=True
        )
        self.thread.start()

    def dispatch(self, body: bytes, key: str) -> tuple[int, dict[str, Any]]:
        future = asyncio.run_coroutine_threadsafe(
            _dispatch(self.client, body, key), self.loop
        )
        try:
            return future.result(timeout=TIMEOUT_SECONDS + 5)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise ProviderError("timeout") from None

    def close(self) -> None:
        try:
            asyncio.run_coroutine_threadsafe(self.client.aclose(), self.loop).result(
                timeout=10
            )
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=10)
            if not self.thread.is_alive():
                self.loop.close()


class ResponsesProvider:
    """Reusable per job/bridge; call close() to release pooled connections."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.ledger = AttemptLedger(path)
        self._transport: _Transport | None = None
        self._transport_lock = threading.Lock()

    def _shared_transport(self) -> _Transport:
        with self._transport_lock:
            if self._transport is None:
                self._transport = _Transport()
            return self._transport

    def close(self) -> None:
        with self._transport_lock:
            transport, self._transport = self._transport, None
        if transport is not None:
            transport.close()

    def __enter__(self) -> "ResponsesProvider":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def require_ready(self) -> None:
        key = OpenAISettingsStore(self.path).load_for_inference()
        self._validate_authorization(key.get_secret_value())

    @staticmethod
    def _validate_authorization(value: str) -> None:
        if not value or any(not 33 <= ord(character) <= 126 for character in value):
            raise CredentialError(
                "OpenAI inference key is not a valid HTTP credential."
            )

    def complete(
        self,
        run_id: str,
        operation_hash: str,
        messages: list[dict[str, str]],
        name: str,
        schema: dict[str, Any],
        max_output_tokens: int = MAX_OUTPUT_TOKENS,
    ) -> str:
        body = json.dumps(
            build_request(messages, name, schema, max_output_tokens),
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        if len(body) > MAX_BODY_BYTES:
            raise ProviderError("configuration")
        key = OpenAISettingsStore(self.path).load_for_inference()
        self._validate_authorization(key.get_secret_value())
        transport = self._shared_transport()
        with _slots:
            with _active_lock:
                attempt = self.ledger.reserve(
                    run_id, operation_hash, active_attempts=frozenset(_active_attempts)
                )
                _active_attempts.add(attempt)
            try:
                try:
                    status, payload = transport.dispatch(body, key.get_secret_value())
                except Exception:
                    try:
                        _record(
                            lambda: self.ledger.finalize(
                                attempt, UsageCounters(), model=None, service_tier=None
                            )
                        )
                    except Exception:
                        pass  # Unfinalized attempt remains unknown, never free.
                    raise
                # Paid unsuccessful and rejected results count BEFORE any output or
                # evidence checks. Actual model/tier govern pricing, not our request.
                envelope = is_error_envelope(status, payload)
                try:
                    if envelope:
                        _record(
                            lambda: self.ledger.finalize_error_envelope(attempt, status)
                        )
                    else:
                        _record(
                            lambda: self.ledger.finalize(
                                attempt,
                                usage_from_response(payload),
                                model=_metadata(payload.get("model")),
                                service_tier=_metadata(payload.get("service_tier")),
                            )
                        )
                except Exception:
                    raise ProviderError("ledger") from None
                if not 200 <= status < 300:
                    raise ProviderError(
                        "http_client" if status < 500 else "http_server",
                        transient=envelope and _transient(status, payload),
                    )
                return response_text(payload)
            finally:
                with _active_lock:
                    _active_attempts.discard(attempt)
