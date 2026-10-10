"""Admin-only custom OpenAI-compatible server checks with SSRF guards.

The base URL must be http(s) without userinfo, query or fragment. Its host is
resolved once; link-local/metadata, unspecified, loopback and the app's own
container subnet are refused, RFC1918, CGNAT/Tailscale and public addresses are
allowed. The connection is pinned to the validated address (no re-resolution,
so DNS rebinding cannot redirect it), redirects are never followed, proxies are
ignored and bodies are size-capped. Errors are fixed categories, never bodies.
"""

import ipaddress
import json
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel

MAX_MODELS_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 10.0
_BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "169.254.0.0/16",  # link-local, cloud metadata
        "fe80::/10",  # IPv6 link-local
        "0.0.0.0/8",
        "::/128",
        "127.0.0.0/8",  # container loopback
        "::1/128",
        "172.30.87.0/29",  # this app's compose subnet
        "224.0.0.0/4",
        "ff00::/8",
        "255.255.255.255/32",
    )
)

ErrorCategory = Literal[
    "invalid_url",
    "blocked_address",
    "dns",
    "timeout",
    "connection",
    "http_status",
    "redirect",
    "response_size",
    "response_schema",
]


class CustomServerError(Exception):
    def __init__(self, category: ErrorCategory) -> None:
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class ValidatedEndpoint:
    base_url: str
    scheme: str
    host: str
    port: int
    path: str
    address: str


Resolver = Callable[[str, int], list[str]]


def _system_resolver(host: str, port: int) -> list[str]:
    return [
        str(info[4][0])
        for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    ]


def address_allowed(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    embedded = _embedded_ipv4(ip) if isinstance(ip, ipaddress.IPv6Address) else None
    candidates = [ip] if embedded is None else [ip, embedded]
    return not any(
        candidate in network
        for candidate in candidates
        for network in _BLOCKED_NETWORKS
    )


_IPV4_COMPATIBLE = ipaddress.ip_network("::/96")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def _embedded_ipv4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """IPv4 reachable through an IPv6 form: mapped, compatible, NAT64 or 6to4."""
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip.sixtofour is not None:
        return ip.sixtofour
    if ip in _NAT64 or (ip in _IPV4_COMPATIBLE and int(ip) > 1):
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def validate_base_url(
    raw: str, resolver: Resolver = _system_resolver
) -> ValidatedEndpoint:
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        raise CustomServerError("invalid_url") from None
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or "@" in parts.netloc
        or len(raw) > 2048
        or any(ch.isspace() for ch in raw)
    ):
        raise CustomServerError("invalid_url")
    host = parts.hostname.lower()
    port = port or (443 if parts.scheme == "https" else 80)
    try:
        literal = ipaddress.ip_address(host)
        addresses = [str(literal)]
    except ValueError:
        try:
            addresses = resolver(host, port)
        except (OSError, UnicodeError):
            raise CustomServerError("dns") from None
    if not addresses:
        raise CustomServerError("dns")
    # Every answer must be allowed so a mixed answer cannot be raced.
    if not all(address_allowed(address) for address in addresses):
        raise CustomServerError("blocked_address")
    path = parts.path.rstrip("/")
    return ValidatedEndpoint(
        base_url=f"{parts.scheme}://{parts.netloc}{path}",
        scheme=parts.scheme,
        host=host,
        port=port,
        path=path,
        address=addresses[0],
    )


def _pinned_get(
    endpoint: ValidatedEndpoint,
    path: str,
    token: str | None,
    transport: httpx.BaseTransport | None = None,
) -> tuple[int, bytes, float]:
    address = endpoint.address
    netloc = f"[{address}]" if ":" in address else address
    url = f"{endpoint.scheme}://{netloc}:{endpoint.port}{endpoint.path}{path}"
    headers = {
        "Host": endpoint.host
        if endpoint.port in (80, 443)
        else f"{endpoint.host}:{endpoint.port}",
        "Accept": "application/json",
        "Accept-Encoding": "identity",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    extensions = {"sni_hostname": endpoint.host} if endpoint.scheme == "https" else {}
    started = time.monotonic()
    try:
        with httpx.Client(
            trust_env=False,
            follow_redirects=False,
            timeout=TIMEOUT_SECONDS,
            transport=transport,
        ) as client:
            with client.stream(
                "GET", url, headers=headers, extensions=extensions
            ) as response:
                if 300 <= response.status_code < 400:
                    raise CustomServerError("redirect")
                declared = response.headers.get("Content-Length")
                if declared is not None and (
                    not declared.isdigit() or int(declared) > MAX_MODELS_BYTES
                ):
                    raise CustomServerError("response_size")
                body = bytearray()
                for chunk in response.iter_bytes(65536):
                    body.extend(chunk)
                    if len(body) > MAX_MODELS_BYTES:
                        raise CustomServerError("response_size")
                return (
                    response.status_code,
                    bytes(body),
                    (time.monotonic() - started) * 1000,
                )
    except CustomServerError:
        raise
    except httpx.TimeoutException:
        raise CustomServerError("timeout") from None
    except Exception:
        raise CustomServerError("connection") from None


class ConnectionTest(BaseModel):
    reachable: bool
    status_code: int | None
    latency_ms: int | None
    model_count: int | None
    error_category: ErrorCategory | None


class ReasoningMetadata(BaseModel):
    kind: Literal["levels", "toggle", "unknown"]
    efforts: list[str]
    default: str | None
    request_field: str | None
    disable_with: dict[str, str | bool | int | None] | None


class CustomModel(BaseModel):
    id: str
    name: str
    loaded: bool | None
    context_window: int | None
    supports_tools: bool | None
    reasoning: ReasoningMetadata | None


def _models_payload(body: bytes) -> list[dict[str, Any]]:
    try:
        payload = json.loads(body, parse_constant=lambda _: None)
    except (ValueError, UnicodeError):
        raise CustomServerError("response_schema") from None
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, list):
        raise CustomServerError("response_schema")
    return [item for item in data[:500] if isinstance(item, dict)]


def _int(value: object) -> int | None:
    return (
        value
        if isinstance(value, int)
        and not isinstance(value, bool)
        and 0 < value < 100_000_000
        else None
    )


def _short(value: object, limit: int = 200) -> str | None:
    return value[:limit] if isinstance(value, str) and value.strip() else None


def _context_window(item: dict[str, Any]) -> int | None:
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    for value in (
        item.get("context_length"),  # strata, OpenRouter
        item.get("loaded_context_length"),  # LM Studio (loaded instance)
        item.get("max_context_length"),  # LM Studio
        item.get("max_model_len"),  # vLLM
        meta.get("n_ctx_train") if isinstance(meta, dict) else None,  # llama.cpp
    ):
        if (parsed := _int(value)) is not None:
            return parsed
    return None


_STANDARD_EFFORTS = ["off", "minimal", "low", "medium", "high"]


def _reasoning(item: dict[str, Any]) -> ReasoningMetadata | None:
    import re

    from waterlooworks_flash_next.ai_settings import (
        EFFORT_PATTERN,
        FIELD_PATTERN,
        RESERVED_REQUEST_FIELDS,
    )

    raw = item.get("reasoning")
    capabilities = item.get("capabilities")
    if not isinstance(raw, dict):
        if isinstance(capabilities, dict) and capabilities.get("reasoning") is True:
            return ReasoningMetadata(
                kind="unknown",
                efforts=_STANDARD_EFFORTS,
                default=None,
                request_field="reasoning_effort",
                disable_with=None,
            )
        return None
    kind: Literal["levels", "toggle", "unknown"] = (
        "levels"
        if raw.get("kind") == "levels"
        else "toggle"
        if raw.get("kind") == "toggle"
        else "unknown"
    )
    efforts = (
        [
            value
            for value in raw.get("efforts", [])
            if isinstance(value, str) and re.fullmatch(EFFORT_PATTERN, value)
        ][:16]
        if isinstance(raw.get("efforts"), list)
        else []
    )
    field = raw.get("request_field")
    field = (
        field
        if isinstance(field, str)
        and re.fullmatch(FIELD_PATTERN, field)
        and field not in RESERVED_REQUEST_FIELDS
        else None
    )
    disable = raw.get("disable_with")
    disable_with: dict[str, str | bool | int | None] | None = None
    if isinstance(disable, dict) and 0 < len(disable) <= 4:
        if all(
            isinstance(key, str)
            and re.fullmatch(FIELD_PATTERN, key)
            and key not in RESERVED_REQUEST_FIELDS
            and (
                value is None
                or isinstance(value, bool | int)
                or (isinstance(value, str) and len(value) <= 40)
            )
            for key, value in disable.items()
        ):
            disable_with = dict(disable)
    if raw.get("can_disable") is True and "off" not in efforts:
        efforts = ["off", *efforts]
    default = raw.get("default")
    return ReasoningMetadata(
        kind=kind,
        efforts=efforts or _STANDARD_EFFORTS,
        default=default if isinstance(default, str) and default in efforts else None,
        request_field=field or "reasoning_effort",
        disable_with=disable_with,
    )


def parse_models(body: bytes) -> list[CustomModel]:
    models = []
    for item in _models_payload(body):
        model_id = _short(item.get("id"))
        if model_id is None:
            continue
        capabilities = item.get("capabilities")
        supported = item.get("supported_parameters")
        tools = (
            capabilities.get("tools")
            if isinstance(capabilities, dict)
            and isinstance(capabilities.get("tools"), bool)
            else ("tools" in supported if isinstance(supported, list) else None)
        )
        loaded = item.get("loaded")
        if not isinstance(loaded, bool):
            state = item.get("state")
            loaded = state == "loaded" if isinstance(state, str) else None
        models.append(
            CustomModel(
                id=model_id,
                name=_short(item.get("name")) or model_id,
                loaded=loaded,
                context_window=_context_window(item),
                supports_tools=tools,
                reasoning=_reasoning(item),
            )
        )
    return models


def check_connection(
    base_url: str,
    token: str | None,
    *,
    resolver: Resolver = _system_resolver,
    transport: httpx.BaseTransport | None = None,
) -> ConnectionTest:
    try:
        endpoint = validate_base_url(base_url, resolver)
        status, body, latency = _pinned_get(endpoint, "/models", token, transport)
    except CustomServerError as error:
        return ConnectionTest(
            reachable=False,
            status_code=None,
            latency_ms=None,
            model_count=None,
            error_category=error.category,
        )
    if not 200 <= status < 300:
        return ConnectionTest(
            reachable=True,
            status_code=status,
            latency_ms=round(latency),
            model_count=None,
            error_category="http_status",
        )
    try:
        count = len(_models_payload(body))
    except CustomServerError as error:
        return ConnectionTest(
            reachable=True,
            status_code=status,
            latency_ms=round(latency),
            model_count=None,
            error_category=error.category,
        )
    return ConnectionTest(
        reachable=True,
        status_code=status,
        latency_ms=round(latency),
        model_count=count,
        error_category=None,
    )


def list_models(
    base_url: str,
    token: str | None,
    *,
    resolver: Resolver = _system_resolver,
    transport: httpx.BaseTransport | None = None,
) -> list[CustomModel]:
    endpoint = validate_base_url(base_url, resolver)
    status, body, _ = _pinned_get(endpoint, "/models", token, transport)
    if not 200 <= status < 300:
        raise CustomServerError("http_status")
    return parse_models(body)
