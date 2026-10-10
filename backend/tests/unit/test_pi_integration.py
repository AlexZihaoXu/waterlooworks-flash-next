"""End-to-end Pi extraction through the REAL built Node sidecar.

A local fake OpenAI-compatible server on 127.0.0.1 scripts the model's tool
calls; nothing reaches a real service. The sidecar runs under the Node
permission model with a scrubbed environment exactly as in production. Skipped
only when the sidecar has not been built (`npm --prefix inference-sidecar run
build`), which the repository check always does first.
"""

import json
import secrets
import shutil
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from waterlooworks_flash_next import inference
from waterlooworks_flash_next.ai_custom import ValidatedEndpoint
from waterlooworks_flash_next.ai_settings import AIConfig, AISettingsStore, CustomToken
from waterlooworks_flash_next.database import open_database
from waterlooworks_flash_next.inference_prompt import build_pi_prompt
from waterlooworks_flash_next.openai_ledger import AttemptLedger, BudgetLimits
from waterlooworks_flash_next.pi_bridge import PiSession, SidecarError, sidecar_command

try:
    sidecar_command()
    BUILT = shutil.which("node") is not None
except SidecarError:
    BUILT = False

pytestmark = pytest.mark.skipif(not BUILT, reason="inference-sidecar is not built")

PAY = "Pay starts at USD 25 per hour for 35 hours/week"
SUBMISSION = {
    "base_rates": [
        {
            "bound_type": "minimum",
            "minimum": "25",
            "maximum": None,
            "minimum_inclusive": True,
            "maximum_inclusive": None,
            "currency": "USD",
            "period": "hour",
            "hours_per_week": "35",
            "payout_frequency": None,
            "eligibility": None,
            # Cited by reference: the backend reconstructs the exact text.
            "evidence": {
                "field": "Compensation and Benefits:",
                "text": None,
                "start": 0,
                "end": len(PAY),
            },
            "eligibility_evidence": None,
        }
    ],
    "skills": [],
}


class FakeModel:
    def __init__(self) -> None:
        self.turns: list[tuple[str, dict[str, Any]]] = []
        self.requests: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []


def _server(model: FakeModel) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            pass

        def do_POST(self) -> None:  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            model.requests.append(body)
            model.headers.append(
                {"authorization": self.headers.get("authorization", "")}
            )
            name, arguments = model.turns.pop(0)
            chunks = [
                {
                    "id": "c",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": f"call_{len(model.requests)}",
                                        "type": "function",
                                        "function": {
                                            "name": name,
                                            "arguments": json.dumps(arguments),
                                        },
                                    }
                                ],
                            },
                            "finish_reason": None,
                        }
                    ],
                },
                {
                    "id": "c",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": body["model"],
                    "choices": [
                        {"index": 0, "delta": {}, "finish_reason": "tool_calls"}
                    ],
                },
                {
                    "id": "c",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": body["model"],
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 400,
                        "completion_tokens": 40,
                        "total_tokens": 440,
                    },
                },
            ]
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            for chunk in chunks:
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")

    return ThreadingHTTPServer(("127.0.0.1", 0), Handler)


@pytest.fixture
def fake_model() -> Iterator[tuple[FakeModel, int]]:
    model = FakeModel()
    server = _server(model)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield model, server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def test_real_sidecar_runs_a_tool_loop_and_backend_accepts_the_submission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_model: tuple[FakeModel, int],
) -> None:
    model, port = fake_model
    monkeypatch.setenv("WW_CREDENTIALS_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-the-sidecar")
    path = tmp_path / "empty.sqlite3"
    open_database(path).close()
    token = secrets.token_urlsafe(24)
    AISettingsStore(path).save_custom_token(CustomToken(token=SecretStr(token)))
    base = f"http://127.0.0.1:{port}/v1"
    config = AIConfig(
        access_mode="custom",
        model_id="local-model",
        custom_base_url=base,
        custom_context_window=32768,
        max_turns=4,
    )
    # Loopback is refused by validate_base_url in production; the test pins
    # the validated endpoint object directly to reach the local fake server.
    endpoint = ValidatedEndpoint(base, "http", "127.0.0.1", port, "/v1", "127.0.0.1")
    fields = {"Compensation and Benefits:": PAY, "Job Summary:": "Build tools."}
    prompt, input_hash = build_pi_prompt(
        fields,
        request_compensation=True,
        request_skills=False,
        deterministic_skills=(),
        config=config,
    )
    model.turns = [
        ("find_span", {"query": "per hour"}),
        ("check_pay", {"text": PAY}),
        ("submit_extraction", SUBMISSION),
    ]
    run_id = str(uuid4())
    AttemptLedger(path).create_run(run_id, BudgetLimits(request_limit=10))
    item = inference._Item(
        run_id, "1", "a" * 64, input_hash, fields, prompt, True, False, ()
    )
    with PiSession(path, config, run_id, endpoint=endpoint) as session:
        result = inference._complete(item, session)
    assert result.rates[0].currency == "USD" and str(result.rates[0].minimum) == "25"
    assert session.home_was_empty is True
    assert len(model.requests) == 3
    assert {h["authorization"] for h in model.headers} == {f"Bearer {token}"}
    tools = sorted(t["function"]["name"] for t in model.requests[0]["tools"])
    assert tools == [
        "check_pay",
        "find_span",
        "list_sections",
        "read_section",
        "submit_extraction",
    ]
    # Small postings are fully inline in the compact header.
    first_user = json.dumps(model.requests[0]["messages"])
    assert "inline_text" in first_user and "Build tools." in first_user
    rows = AttemptLedger(path).summary(run_id)
    assert (rows.attempts, rows.unpriced_attempts) == (3, 0)
    assert rows.known_usd == 0
