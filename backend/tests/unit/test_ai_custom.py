"""Custom server SSRF guards and /models parsing with an in-process transport.

No socket is opened: resolution is a test resolver and HTTP is an
httpx.MockTransport, so nothing reaches a real host.
"""

import json
import secrets

import httpx
import pytest

from waterlooworks_flash_next.ai_custom import (
    CustomServerError,
    address_allowed,
    check_connection,
    list_models,
    parse_models,
    validate_base_url,
)


def resolver(*answers: str):  # type: ignore[no-untyped-def]
    return lambda _host, _port: list(answers)


@pytest.mark.parametrize(
    "address",
    [
        "169.254.169.254",
        "169.254.1.1",
        "fe80::1",
        "0.0.0.0",
        "::",
        "127.0.0.1",
        "::1",
        "172.30.87.3",
        "::ffff:169.254.169.254",
        "::ffff:127.0.0.1",
        "::169.254.169.254",  # IPv4-compatible
        "::127.0.0.1",
        "::ac1e:5703",  # IPv4-compatible 172.30.87.3
        "64:ff9b::a9fe:a9fe",  # NAT64 169.254.169.254
        "64:ff9b::7f00:1",  # NAT64 127.0.0.1
        "2002:a9fe:a9fe::1",  # 6to4 169.254.169.254
        "2002:7f00:1::1",  # 6to4 127.0.0.1
    ],
)
def test_blocked_addresses(address: str) -> None:
    assert not address_allowed(address)
    with pytest.raises(CustomServerError) as error:
        validate_base_url("http://model.example/v1", resolver(address))
    assert error.value.category == "blocked_address"


@pytest.mark.parametrize(
    "address", ["10.0.0.5", "192.168.1.20", "172.16.0.9", "100.64.0.10", "8.8.8.8"]
)
def test_allowed_private_cgnat_and_public(address: str) -> None:
    endpoint = validate_base_url("https://model.example:8443/v1/", resolver(address))
    assert endpoint.address == address
    assert endpoint.base_url == "https://model.example:8443/v1"
    assert (endpoint.host, endpoint.port, endpoint.path) == (
        "model.example",
        8443,
        "/v1",
    )


def test_every_resolved_answer_must_be_allowed() -> None:
    with pytest.raises(CustomServerError):
        validate_base_url(
            "http://model.example/v1", resolver("10.0.0.5", "169.254.169.254")
        )


@pytest.mark.parametrize(
    "url",
    [
        "ftp://model.example/v1",
        "http://user:pass@model.example/v1",
        "http://model.example/v1?x=1",
        "http://model.example/v1#frag",
        "file:///etc/passwd",
        "http:///v1",
        "http://model.example/v 1",
    ],
)
def test_invalid_urls(url: str) -> None:
    with pytest.raises(CustomServerError) as error:
        validate_base_url(url, resolver("10.0.0.5"))
    assert error.value.category == "invalid_url"


def test_literal_ip_is_checked_without_resolution() -> None:
    def never(_host: str, _port: int) -> list[str]:
        raise AssertionError("literal addresses are not resolved")

    with pytest.raises(CustomServerError):
        validate_base_url("http://169.254.169.254/latest", never)
    assert validate_base_url("http://[fd00::5]:8000/v1", never).address == "fd00::5"


STRATA = {
    "data": [
        {
            "id": "ctx384k",
            "name": "SC117 Qwen 3.8 Flash Next",
            "loaded": True,
            "context_length": 393216,
            "capabilities": {"chat": True, "tools": True, "reasoning": True},
            "supported_parameters": ["tools", "tool_choice", "reasoning_effort"],
            "reasoning": {
                "kind": "levels",
                "efforts": ["off", "low", "medium", "xhigh"],
                "default": "medium",
                "can_disable": True,
                "request_field": "reasoning_effort",
                "disable_with": {"reasoning_effort": "none"},
                "response_field": "reasoning_content",
            },
        },
        {
            "id": "toggle-model",
            "loaded": False,
            "reasoning": {"kind": "toggle", "efforts": ["on", "off"]},
        },
    ]
}


def test_models_parse_strata_and_common_server_shapes() -> None:
    models = parse_models(json.dumps(STRATA).encode())
    strata = models[0]
    assert (strata.id, strata.loaded, strata.context_window, strata.supports_tools) == (
        "ctx384k",
        True,
        393216,
        True,
    )
    assert strata.reasoning is not None
    assert strata.reasoning.efforts == ["off", "low", "medium", "xhigh"]
    assert strata.reasoning.default == "medium"
    assert strata.reasoning.disable_with == {"reasoning_effort": "none"}
    toggle = models[1]
    assert toggle.reasoning is not None and toggle.reasoning.kind == "toggle"
    assert toggle.reasoning.efforts == ["on", "off"]
    others = parse_models(
        json.dumps(
            {
                "data": [
                    {"id": "lmstudio", "max_context_length": 8192, "state": "loaded"},
                    {"id": "lmstudio-loaded", "loaded_context_length": 4096},
                    {"id": "vllm", "max_model_len": 32768},
                    {"id": "router", "context_length": 128000},
                    {"id": "llamacpp", "meta": {"n_ctx_train": 65536}},
                    {"id": "unknown"},
                    {"name": "no id is skipped"},
                ]
            }
        ).encode()
    )
    assert [(m.id, m.context_window) for m in others] == [
        ("lmstudio", 8192),
        ("lmstudio-loaded", 4096),
        ("vllm", 32768),
        ("router", 128000),
        ("llamacpp", 65536),
        ("unknown", None),
    ]
    assert others[0].loaded is True
    with pytest.raises(CustomServerError):
        parse_models(b"not json")
    hostile = parse_models(
        json.dumps(
            {
                "data": [
                    {
                        "id": "x",
                        "reasoning": {
                            "kind": "levels",
                            "efforts": ["low", "DROP TABLE"],
                            "request_field": "messages",
                            "disable_with": {"model": "other"},
                        },
                    }
                ]
            }
        ).encode()
    )[0]
    assert hostile.reasoning is not None
    assert hostile.reasoning.efforts == ["low"]
    assert hostile.reasoning.request_field == "reasoning_effort"
    assert hostile.reasoning.disable_with is None


def test_requests_are_pinned_unredirected_and_never_leak_bodies() -> None:
    token = secrets.token_urlsafe(24)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=STRATA)

    models = list_models(
        "https://model.example:8443/v1",
        token,
        resolver=resolver("100.64.0.10"),
        transport=httpx.MockTransport(handler),
    )
    assert [m.id for m in models] == ["ctx384k", "toggle-model"]
    request = seen[0]
    assert request.url.host == "100.64.0.10"  # connection pinned to the IP
    assert request.url.path == "/v1/models"
    assert request.headers["host"] == "model.example:8443"
    assert request.headers["authorization"] == f"Bearer {token}"
    assert request.extensions.get("sni_hostname") == "model.example"

    def redirect(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://169.254.169.254/"})

    result = check_connection(
        "http://model.example/v1",
        None,
        resolver=resolver("10.0.0.5"),
        transport=httpx.MockTransport(redirect),
    )
    assert (result.reachable, result.error_category) == (False, "redirect")

    def failing(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=f"bad token {token}")

    result = check_connection(
        "http://model.example/v1",
        token,
        resolver=resolver("10.0.0.5"),
        transport=httpx.MockTransport(failing),
    )
    assert result.status_code == 401 and result.error_category == "http_status"
    assert token not in result.model_dump_json()

    def huge(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (3 * 1024 * 1024))

    result = check_connection(
        "http://model.example/v1",
        None,
        resolver=resolver("10.0.0.5"),
        transport=httpx.MockTransport(huge),
    )
    assert result.error_category == "response_size"
    ok = check_connection(
        "http://model.example/v1",
        None,
        resolver=resolver("10.0.0.5"),
        transport=httpx.MockTransport(handler),
    )
    assert (ok.reachable, ok.status_code, ok.model_count, ok.error_category) == (
        True,
        200,
        2,
        None,
    )
    blocked = check_connection(
        "http://metadata.example/", None, resolver=resolver("169.254.169.254")
    )
    assert blocked.error_category == "blocked_address"


def test_translated_ipv6_of_allowed_ipv4_stays_allowed() -> None:
    assert address_allowed("64:ff9b::808:808")
    assert address_allowed("2002:a00:5::1")
    assert address_allowed("2001:4860:4860::8888")
