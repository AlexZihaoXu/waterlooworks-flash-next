"""One private stdin/stdout Responses worker per CLI run; no public proxy."""

import concurrent.futures
import json
import queue
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO, Literal

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from waterlooworks_flash_next.openai_ledger import BudgetLimits, BudgetStopped
from waterlooworks_flash_next.openai_provider import ProviderError, ResponsesProvider
from waterlooworks_flash_next.provider_lease import ProviderBusy, ProviderLease

MAX_LINE_BYTES = 2 * 1024 * 1024
MAX_LIFETIME_SECONDS = 24 * 60 * 60


class BridgeMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    id: int = Field(ge=0, le=2**31 - 1)
    op: Literal["initialize", "bind", "complete"]
    database: str | None = Field(default=None, max_length=4096, repr=False)
    load_environment: bool = False
    run_id: str | None = Field(default=None, pattern=r"^[0-9a-f-]{36}$")
    owner: str | None = Field(default=None, pattern=r"^[0-9a-f-]{36}$")
    kind: Literal["extraction", "country"] | None = None
    operation_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    messages: list[dict[str, str]] | None = Field(
        default=None, max_length=3, repr=False
    )
    schema_: dict[str, Any] | None = Field(default=None, alias="schema", repr=False)
    name: str | None = Field(default=None, pattern=r"^[a-z_]{1,64}$")
    max_output_tokens: int = Field(default=4096, ge=1, le=4096)


def read_messages(stream: BinaryIO, output: queue.Queue[bytes | None]) -> None:
    while True:
        line = stream.readline(MAX_LINE_BYTES + 1)
        if not line:
            output.put(None)
            return
        output.put(line)
        if len(line) > MAX_LINE_BYTES or not line.endswith(b"\n"):
            return


class Bridge:
    def __init__(self) -> None:
        self.lease: ProviderLease | None = None
        self.provider: ResponsesProvider | None = None
        self.run_id: str | None = None
        self.owner: str | None = None
        self.kind: str | None = None

    def initialize(self, message: BridgeMessage) -> None:
        if self.provider is not None or message.database is None:
            raise ProviderError("configuration")
        path = Path(message.database).resolve(strict=True)
        self.lease = ProviderLease(path).acquire()
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            if connection.execute("PRAGMA user_version").fetchone()[0] != 14:
                raise ProviderError("configuration")
            if connection.execute(
                "SELECT 1 FROM collection_runs WHERE status='running'"
            ).fetchone():
                raise ProviderBusy("Collection owns this database.")
        finally:
            connection.close()
        if message.load_environment:
            # Explicit operator CLI work only, never import/status/tests. Node does
            # not parse backend secret files or receive their values.
            load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False)
        self.provider = ResponsesProvider(path)
        self.provider.require_ready()
        BudgetLimits.from_environment()

    def require_owner(self) -> None:
        if (
            self.provider is None
            or self.lease is None
            or self.run_id is None
            or self.owner is None
        ):
            raise ProviderError("configuration")
        self.lease.require_owner(self.provider.path)
        table = (
            "source_extraction_runs"
            if self.kind == "extraction"
            else "country_inference_runs"
        )
        connection = sqlite3.connect(self.provider.path.as_uri() + "?mode=ro", uri=True)
        try:
            row = connection.execute(
                "SELECT status,owner,lease_until_ms,stop_requested "
                f"FROM {table} WHERE run_id=?",
                (self.run_id,),
            ).fetchone()
            if (
                row is None
                or row[0] != "running"
                or row[1] != self.owner
                or row[2] < time.time() * 1000
                or row[3]
            ):
                raise ProviderBusy("CLI ownership is unavailable.")
            if connection.execute(
                "SELECT 1 FROM collection_runs WHERE status='running'"
            ).fetchone():
                raise ProviderBusy("Collection owns this database.")
        finally:
            connection.close()

    def bind(self, message: BridgeMessage) -> None:
        if (
            self.run_id is not None
            or message.run_id is None
            or message.owner is None
            or message.kind is None
        ):
            raise ProviderError("configuration")
        self.run_id, self.owner, self.kind = message.run_id, message.owner, message.kind
        self.require_owner()
        assert self.provider is not None
        self.provider.ledger.create_run(self.run_id, BudgetLimits.from_environment())
        self.provider.ledger.require_resumable(self.run_id)

    def complete(self, message: BridgeMessage) -> str:
        self.require_owner()
        if (
            message.operation_hash is None
            or message.messages is None
            or message.schema_ is None
            or message.name is None
        ):
            raise ProviderError("configuration")
        assert self.provider is not None and self.run_id is not None
        text = self.provider.complete(
            self.run_id,
            message.operation_hash,
            message.messages,
            message.name,
            message.schema_,
            message.max_output_tokens,
        )
        if len(text.encode("utf-8")) > 256 * 1024:
            raise ProviderError("response_size")
        return text

    def close(self) -> None:
        if self.lease is not None:
            self.lease.close()


def main() -> None:
    incoming: queue.Queue[bytes | None] = queue.Queue(maxsize=1)
    threading.Thread(
        target=read_messages, args=(sys.stdin.buffer, incoming), daemon=True
    ).start()
    write_lock = threading.Lock()
    slots = threading.BoundedSemaphore(4)
    deadline = time.monotonic() + MAX_LIFETIME_SECONDS
    bridge = Bridge()
    last_id = -1

    def send(value: dict[str, Any]) -> None:
        with write_lock:
            sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
            sys.stdout.flush()

    def execute(message: BridgeMessage) -> None:
        try:
            text = bridge.complete(message)
            send({"id": message.id, "ok": True, "text": text})
        except BudgetStopped:
            send({"id": message.id, "ok": False, "error": "budget_stopped"})
        except Exception:
            send({"id": message.id, "ok": False, "error": "provider_failed"})
        finally:
            slots.release()

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            while time.monotonic() < deadline:
                raw = incoming.get(timeout=min(180, deadline - time.monotonic()))
                if raw is None:
                    break
                if len(raw) > MAX_LINE_BYTES or not raw.endswith(b"\n"):
                    raise ProviderError("configuration")
                message = BridgeMessage.model_validate_json(raw)
                if message.id <= last_id:
                    raise ProviderError("configuration")
                last_id = message.id
                if message.op == "initialize":
                    bridge.initialize(message)
                    send({"id": message.id, "ok": True})
                elif message.op == "bind":
                    bridge.bind(message)
                    send({"id": message.id, "ok": True})
                else:
                    if not slots.acquire(blocking=False):
                        raise ProviderBusy("Bridge concurrency exceeded.")
                    pool.submit(execute, message)
    except (Exception, ValidationError):
        try:
            send({"id": max(last_id, 0), "ok": False, "error": "bridge_unavailable"})
        except Exception:
            pass
    finally:
        bridge.close()


if __name__ == "__main__":
    main()
