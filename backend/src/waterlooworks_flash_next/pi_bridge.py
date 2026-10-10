"""Spawn and drive the disposable Pi sidecar over strict JSON lines.

One sidecar process per inference run. The backend stays authoritative: it
builds every prompt and section, answers credential/ledger/tool RPCs,
validates submissions and writes results. The child runs under the Node
permission model (reads limited to its own install directory, no writes, no
child processes, no workers) with a scrubbed environment and an empty private
HOME that is verified empty and removed afterwards. Child stderr is discarded
and nothing the child sends is ever echoed into errors or logs.
"""

import concurrent.futures
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import IO, Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from waterlooworks_flash_next.ai_custom import ValidatedEndpoint
from waterlooworks_flash_next.ai_settings import (
    AIConfig,
    AISettingsStore,
    OAuthCredential,
)
from waterlooworks_flash_next.credentials import CredentialError
from waterlooworks_flash_next.openai_ledger import AttemptLedger, BudgetStopped
from waterlooworks_flash_next.openai_usage import MODEL as PRICED_MODEL
from waterlooworks_flash_next.openai_usage import UsageCounters
from waterlooworks_flash_next.runtime_secrets import runtime_value

PROTOCOL_VERSION = 1
MAX_LINE_BYTES = 4 * 1024 * 1024
START_TIMEOUT_SECONDS = 30.0
PI_CATALOG_PRICING = "pi-ai-1.1.0-catalog"
_DEFAULT_DIR = Path(__file__).resolve().parents[3] / "inference-sidecar"
_CATEGORY = r"^[a-z_]{1,40}$"
_ITEM_ID = r"^[a-z0-9][a-z0-9-]{0,79}$"


class SidecarError(Exception):
    """Fixed category only; never contains child output or secrets."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def sidecar_command(sidecar_dir: Path | None = None) -> tuple[list[str], Path]:
    """Node binary + permission flags + entrypoint; raises when not installed."""
    root = (
        sidecar_dir or Path(runtime_value("WW_SIDECAR_DIR") or _DEFAULT_DIR)
    ).resolve()
    main = root / "dist" / "src" / "main.js"
    node = runtime_value("WW_NODE_BINARY") or shutil.which("node")
    if not node or not os.path.isabs(node) or not main.is_file():
        raise SidecarError("sidecar_unavailable")
    return (
        [
            node,
            "--permission",
            f"--allow-fs-read={root}",
            "--no-addons",
            "--max-old-space-size=512",
            str(main),
        ],
        root,
    )


def require_sidecar() -> None:
    try:
        sidecar_command()
    except SidecarError:
        raise CredentialError("Pi sidecar is not installed.") from None


def _scrubbed_environment(home: str) -> dict[str, str]:
    # Nothing inherited: no proxies, tokens, NODE_OPTIONS or backend secrets.
    return {
        "HOME": home,
        "XDG_CONFIG_HOME": home,
        "XDG_CACHE_HOME": home,
        "XDG_DATA_HOME": home,
        "XDG_STATE_HOME": home,
        "TMPDIR": home,
        "TZ": "UTC",
        "LANG": "C.UTF-8",
        "NODE_ENV": "production",
    }


def catalog(provider: Literal["openai", "openai-codex"]) -> list[dict[str, Any]]:
    """pi-ai's static model catalog (no network), read through the sidecar."""
    command, _ = sidecar_command()
    home = tempfile.mkdtemp(prefix="ww-pi-")
    try:
        result = subprocess.run(
            [*command, "catalog", provider],
            cwd=home,
            env=_scrubbed_environment(home),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SidecarError("sidecar_unavailable") from None
    finally:
        shutil.rmtree(home, ignore_errors=True)
    if result.returncode != 0 or len(result.stdout) > MAX_LINE_BYTES:
        raise SidecarError("sidecar_unavailable")
    try:
        models = _CatalogPayload.model_validate_json(result.stdout).models
    except ValidationError:
        raise SidecarError("protocol") from None
    return [model.model_dump() for model in models]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class _CatalogModel(_Strict):
    id: str = Field(max_length=200)
    name: str = Field(max_length=200)
    context_window: int = Field(ge=0)
    max_output_tokens: int = Field(ge=0)
    reasoning: bool
    efforts: list[str] = Field(max_length=16)


class _CatalogPayload(_Strict):
    models: list[_CatalogModel] = Field(max_length=500)


class _Ready(_Strict):
    type: Literal["ready"]
    provider_id: str = Field(max_length=40)
    model_id: str = Field(max_length=200)


class _Rpc(_Strict):
    type: Literal["rpc"]
    id: int = Field(ge=0, le=2**31 - 1)
    method: Literal[
        "credential.read",
        "credential.list",
        "credential.lock",
        "credential.commit",
        "credential.release",
        "credential.delete",
        "ledger.reserve",
        "ledger.finalize",
        "tool.check_pay",
        "submission.check",
    ]
    params: dict[str, Any]


class _ItemResult(_Strict):
    type: Literal["item_result"]
    item_id: str = Field(pattern=_ITEM_ID)
    status: Literal["submitted", "failed"]
    payload: dict[str, Any] | None
    category: str | None = Field(pattern=_CATEGORY)
    turns: int = Field(ge=0, le=100)
    tool_calls: int = Field(ge=0, le=1000)


class _Fatal(_Strict):
    type: Literal["fatal"]
    category: str = Field(pattern=_CATEGORY)


class _Done(_Strict):
    type: Literal["done"]


_InboundMessage = _Ready | _Rpc | _ItemResult | _Fatal | _Done
_Inbound: TypeAdapter[_InboundMessage] = TypeAdapter(
    Annotated[_InboundMessage, Field(discriminator="type")]
)


class _ProviderParams(_Strict):
    provider_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,39}$")


class _LockParams(_Strict):
    lock: str = Field(pattern=r"^[0-9a-f]{32}$")


class _CommitParams(_LockParams):
    credential: dict[str, Any]


class _ReserveParams(_Strict):
    item_id: str = Field(pattern=_ITEM_ID)
    turn: int = Field(ge=1, le=100)


class _Usage(_Strict):
    input: int | None = Field(ge=0, le=2**53 - 1)
    output: int | None = Field(ge=0, le=2**53 - 1)
    cache_read: int | None = Field(ge=0, le=2**53 - 1)
    cache_write: int | None = Field(ge=0, le=2**53 - 1)
    reasoning: int | None = Field(ge=0, le=2**53 - 1)


class _FinalizeParams(_Strict):
    attempt: str = Field(pattern=r"^[0-9a-f-]{36}$")
    outcome: Literal["completed", "error", "aborted"]
    http_status: int | None = Field(ge=100, le=599)
    model: str | None = Field(max_length=200)
    usage: _Usage
    cost_usd: float | None = Field(default=None, ge=0, le=1_000_000)


class _CheckPayParams(_Strict):
    item_id: str = Field(pattern=_ITEM_ID)
    text: str = Field(min_length=1, max_length=1024)


class _SubmissionParams(_Strict):
    item_id: str = Field(pattern=_ITEM_ID)
    payload: dict[str, Any]


class _Refused(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PiItem:
    """One posting's agent task. `check` validates a submission authoritatively."""

    item_id: str
    kind: Literal["extraction", "country"]
    system: str
    user: str
    sections: tuple[tuple[str, str], ...]
    submit_schema: dict[str, Any]
    operation_key: str
    check: Callable[[dict[str, Any]], tuple[bool, str | None]] = field(repr=False)


@dataclass(frozen=True)
class ItemResult:
    status: Literal["submitted", "failed"]
    payload: dict[str, Any] | None
    category: str | None
    turns: int
    tool_calls: int


@dataclass
class ItemMetrics:
    """Per-item token totals from finalized turns; no content."""

    finalized_turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0

    def add(self, usage: "_Usage") -> None:
        self.finalized_turns += 1
        self.input_tokens += (
            (usage.input or 0) + (usage.cache_read or 0) + (usage.cache_write or 0)
        )
        self.output_tokens += usage.output or 0
        self.reasoning_tokens += usage.reasoning or 0
        self.cached_tokens += usage.cache_read or 0


def usage_counters(usage: _Usage) -> UsageCounters:
    """pi-ai input excludes cache reads/writes; the ledger's input includes them."""
    try:
        if usage.input is None or usage.cache_read is None or usage.cache_write is None:
            return UsageCounters(
                output_tokens=usage.output, reasoning_tokens=usage.reasoning
            )
        return UsageCounters(
            input_tokens=usage.input + usage.cache_read + usage.cache_write,
            cached_tokens=usage.cache_read,
            cache_write_tokens=usage.cache_write,
            output_tokens=usage.output,
            reasoning_tokens=usage.reasoning
            if usage.reasoning is None
            or usage.output is None
            or usage.reasoning <= usage.output
            else None,
        )
    except ValidationError:
        return UsageCounters()


def check_pay(text: str) -> dict[str, Any]:
    """Deterministic pay parse exposed to the model as a tool (no I/O)."""
    from waterlooworks_flash_next.compensation_parser import parse_compensation

    result = parse_compensation(text)
    value = None
    if result.value is not None:
        value = {
            key: (str(item) if isinstance(item, Decimal) else item)
            for key, item in result.value.model_dump().items()
        }
    return {"state": result.state, "reason": result.reason, "value": value}


class PiSession:
    """One sidecar process for one run; `run_item` is thread-safe and blocking."""

    def __init__(
        self,
        path: Path,
        config: AIConfig,
        run_id: str,
        *,
        endpoint: ValidatedEndpoint | None = None,
        sidecar_dir: Path | None = None,
        item_timeout_seconds: float | None = None,
        ledger: Any = None,
    ) -> None:
        if config.model_id is None:
            raise SidecarError("configuration")
        if (config.access_mode == "custom") != (endpoint is not None):
            raise SidecarError("configuration")
        self.path = path
        self.config = config
        self.run_id = run_id
        self.endpoint = endpoint
        self.sidecar_dir = sidecar_dir
        self.store = AISettingsStore(path)
        # Dry-run evaluation injects an in-memory ledger; runs use SQLite.
        self.ledger = ledger if ledger is not None else AttemptLedger(path)
        self._attempt_items: dict[str, str] = {}
        self._metrics: dict[str, ItemMetrics] = {}
        self.item_timeout = item_timeout_seconds or min(
            3600.0,
            config.request_timeout_seconds * (config.max_turns + 1) + 60.0,
        )
        self._process: subprocess.Popen[bytes] | None = None
        self._home: str | None = None
        self._write_lock = threading.Lock()
        self._items: dict[str, PiItem] = {}
        self._waiters: dict[str, concurrent.futures.Future[ItemResult]] = {}
        self._state_lock = threading.Lock()
        self._active_attempts: set[str] = set()
        self._held: dict[str, threading.Lock] = {}
        self._ready = threading.Event()
        self._failed: str | None = None
        self._reader: threading.Thread | None = None
        self._rpc_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="pi-rpc"
        )
        self.home_was_empty: bool | None = None

    # ----- lifecycle -----------------------------------------------------------

    def _start_message(self) -> dict[str, Any]:
        config = self.config
        custom = None
        if self.endpoint is not None:
            custom = {
                "base_url": self.endpoint.base_url,
                "host": self.endpoint.host,
                "pinned_address": self.endpoint.address,
                "context_window": config.custom_context_window or 32768,
                "reasoning_field": config.custom_reasoning_field,
                "disable_with": config.custom_disable_with,
            }
        return {
            "type": "start",
            "protocol": PROTOCOL_VERSION,
            "mode": config.access_mode,
            "model_id": config.model_id,
            "reasoning": config.reasoning_effort,
            "concurrency": config.parallel_streams,
            "max_turns": config.max_turns,
            "max_tool_calls": config.max_tool_calls,
            "max_output_tokens": config.max_output_tokens,
            "request_timeout_ms": config.request_timeout_seconds * 1000,
            "item_timeout_ms": int(self.item_timeout * 1000),
            "custom": custom,
        }

    def start(self) -> "PiSession":
        command, _ = sidecar_command(self.sidecar_dir)
        self._home = tempfile.mkdtemp(prefix="ww-pi-")
        try:
            self._process = subprocess.Popen(
                [*command, "run"],
                cwd=self._home,
                env=_scrubbed_environment(self._home),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except OSError:
            self._cleanup_home()
            raise SidecarError("sidecar_unavailable") from None
        self._reader = threading.Thread(
            target=self._read, name="pi-sidecar-reader", daemon=True
        )
        self._reader.start()
        self._send(self._start_message())
        if not self._ready.wait(START_TIMEOUT_SECONDS) or self._failed:
            category = self._failed or "timeout"
            self.close()
            raise SidecarError(category)
        return self

    def __enter__(self) -> "PiSession":
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        process = self._process
        if process is not None:
            try:
                self._send({"type": "end"})
                if process.stdin:
                    process.stdin.close()
            except Exception:
                pass
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        self._fail_all("connection")
        if self._reader is not None:
            self._reader.join(timeout=5)
        with self._state_lock:
            for lock in self._held.values():
                lock.release()
            self._held.clear()
        self._rpc_pool.shutdown(wait=True, cancel_futures=True)
        self._abandon_attempts()
        self._cleanup_home()

    def _abandon_attempts(self) -> None:
        """Settle attempts the sidecar reserved but never finalized (crash/timeout).

        Runs after the child exited and every RPC finished, so no late finalize
        can race it. Subscription/custom requests are never billed per request:
        they become known $0. Paid API-key attempts stay unknown for operator
        review (``python -m waterlooworks_flash_next.openai_ledger``).
        """
        with self._state_lock:
            abandoned, self._active_attempts = self._active_attempts, set()
        if not self.config.is_zero_cost:
            return
        for attempt in abandoned:
            try:
                self.ledger.finalize_priced(
                    attempt,
                    UsageCounters(),
                    model=None,
                    known_usd=Decimal(0),
                    pricing_version=f"{self.config.pricing_version}-abandoned",
                )
            except Exception:
                pass  # Stays unknown; never assumed free by the ledger.

    def _cleanup_home(self) -> None:
        if self._home is None:
            return
        try:
            self.home_was_empty = not os.listdir(self._home)
        except OSError:
            self.home_was_empty = None
        shutil.rmtree(self._home, ignore_errors=True)
        self._home = None

    # ----- transport -----------------------------------------------------------

    def _send(self, value: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise SidecarError("connection")
        line = json.dumps(
            value, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
        data = (line + "\n").encode("utf-8")
        if len(data) > MAX_LINE_BYTES:
            raise SidecarError("protocol")
        with self._write_lock:
            try:
                process.stdin.write(data)
                process.stdin.flush()
            except (OSError, ValueError):
                raise SidecarError("connection") from None

    def _read(self) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        stream: IO[bytes] = process.stdout
        try:
            while True:
                line = stream.readline(MAX_LINE_BYTES + 1)
                if not line:
                    break
                if len(line) > MAX_LINE_BYTES or not line.endswith(b"\n"):
                    self._fail_all("protocol")
                    break
                try:
                    message = _Inbound.validate_json(line)
                except ValidationError:
                    self._fail_all("protocol")
                    break
                self._dispatch(message)
        except Exception:
            self._fail_all("protocol")
        finally:
            self._fail_all(self._failed or "connection")
            if process.poll() is None:
                process.kill()

    def _dispatch(self, message: _Ready | _Rpc | _ItemResult | _Fatal | _Done) -> None:
        if isinstance(message, _Ready):
            if (
                message.model_id != self.config.model_id
                or message.provider_id != self.config.provider_id
            ):
                self._fail_all("protocol")
                return
            self._ready.set()
        elif isinstance(message, _Rpc):
            self._rpc_pool.submit(self._answer, message)
        elif isinstance(message, _ItemResult):
            with self._state_lock:
                waiter = self._waiters.pop(message.item_id, None)
                self._items.pop(message.item_id, None)
            if waiter is None:
                self._fail_all("protocol")
                return
            waiter.set_result(
                ItemResult(
                    message.status,
                    message.payload if message.status == "submitted" else None,
                    message.category,
                    message.turns,
                    message.tool_calls,
                )
            )
        elif isinstance(message, _Fatal):
            self._fail_all(message.category)
        # `done` needs no action: EOF follows.

    def _fail_all(self, category: str) -> None:
        with self._state_lock:
            if self._failed is None:
                self._failed = category
            waiters = list(self._waiters.items())
            self._waiters.clear()
            self._items.clear()
        self._ready.set()
        for _, waiter in waiters:
            if not waiter.done():
                waiter.set_result(ItemResult("failed", None, category, 0, 0))

    # ----- items ---------------------------------------------------------------

    def item_metrics(self, item_id: str) -> "ItemMetrics":
        """Token usage reported for one item's model turns (counts only)."""
        with self._state_lock:
            return self._metrics.pop(item_id, ItemMetrics())

    def run_item(self, item: PiItem) -> ItemResult:
        future: concurrent.futures.Future[ItemResult] = concurrent.futures.Future()
        with self._state_lock:
            if self._failed is not None:
                return ItemResult("failed", None, self._failed, 0, 0)
            if item.item_id in self._waiters:
                raise SidecarError("protocol")
            self._waiters[item.item_id] = future
            self._items[item.item_id] = item
        try:
            self._send(
                {
                    "type": "item",
                    "item_id": item.item_id,
                    "kind": item.kind,
                    "system": item.system,
                    "user": item.user,
                    "sections": [
                        {"name": name, "text": text} for name, text in item.sections
                    ],
                    "submit_schema": item.submit_schema,
                }
            )
        except SidecarError as error:
            self._fail_all(error.category)
        try:
            return future.result(timeout=self.item_timeout + 30)
        except concurrent.futures.TimeoutError:
            self._fail_all("timeout")
            return ItemResult("failed", None, "timeout", 0, 0)

    # ----- RPC -----------------------------------------------------------------

    def _answer(self, message: _Rpc) -> None:
        try:
            result = self._handle(message.method, message.params)
            reply: dict[str, Any] = {
                "type": "rpc_result",
                "id": message.id,
                "ok": True,
                "result": result,
            }
        except _Refused as refused:
            reply = {
                "type": "rpc_result",
                "id": message.id,
                "ok": False,
                "error": refused.code,
            }
        except BudgetStopped:
            reply = {
                "type": "rpc_result",
                "id": message.id,
                "ok": False,
                "error": "budget_stopped",
            }
        except Exception:
            reply = {
                "type": "rpc_result",
                "id": message.id,
                "ok": False,
                "error": "refused",
            }
        try:
            self._send(reply)
        except SidecarError:
            pass

    def _item(self, item_id: str) -> PiItem:
        with self._state_lock:
            item = self._items.get(item_id)
        if item is None:
            raise _Refused("unknown_item")
        return item

    def _handle(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            if method == "credential.read":
                provider = _ProviderParams.model_validate(params).provider_id
                return {
                    "credential": self.store.credential_for_provider(
                        self.config, provider
                    )
                }
            if method == "credential.list":
                _Strict.model_validate(params)
                credential = self.store.credential_for_provider(
                    self.config, self.config.provider_id
                )
                return {
                    "credentials": []
                    if credential is None
                    else [
                        {
                            "provider_id": self.config.provider_id,
                            "type": credential["type"],
                        }
                    ]
                }
            if method == "credential.lock":
                provider = _ProviderParams.model_validate(params).provider_id
                if provider != self.config.provider_id:
                    raise _Refused("refused")
                lock = self.store.lock(self.config)
                token = secrets.token_hex(16)
                with self._state_lock:
                    self._held[token] = lock
                try:
                    credential = self.store.credential_for_provider(
                        self.config, provider
                    )
                except Exception:
                    self._release(token)
                    raise
                return {"lock": token, "credential": credential}
            if method == "credential.commit":
                commit = _CommitParams.model_validate(params)
                with self._state_lock:
                    if commit.lock not in self._held:
                        raise _Refused("invalid_lock")
                rotated = OAuthCredential.model_validate(commit.credential)
                # Persist the rotated token BEFORE acknowledging the commit. On
                # failure the lock stays held so the sidecar can retry the same
                # token (or release); close() releases anything left.
                self.store.commit_locked(self.config, rotated)
                self._release(commit.lock)
                return {}
            if method == "credential.release":
                self._release(_LockParams.model_validate(params).lock)
                return {}
            if method == "credential.delete":
                raise _Refused("refused")
            if method == "ledger.reserve":
                reserve = _ReserveParams.model_validate(params)
                item = self._item(reserve.item_id)
                # Authoritative per-posting cap, independent of the sidecar.
                if reserve.turn > self.config.max_turns:
                    raise _Refused("turn_limit")
                return {"attempt": self._reserve(item, reserve.turn)}
            if method == "ledger.finalize":
                self._finalize(_FinalizeParams.model_validate(params))
                return {}
            if method == "tool.check_pay":
                pay = _CheckPayParams.model_validate(params)
                self._item(pay.item_id)
                return check_pay(pay.text)
            if method == "submission.check":
                submission = _SubmissionParams.model_validate(params)
                item = self._item(submission.item_id)
                accepted, hint = item.check(submission.payload)
                return {"accepted": accepted} | (
                    {} if accepted else {"hint": hint or "Rejected."}
                )
        except ValidationError:
            raise _Refused("invalid_params") from None
        except CredentialError:
            raise _Refused("credential_unavailable") from None
        raise _Refused("unknown_method")

    def _release(self, token: str) -> None:
        with self._state_lock:
            lock = self._held.pop(token, None)
        if lock is not None:
            lock.release()

    def _reserve(self, item: PiItem, turn: int) -> str:
        operation = hashlib.sha256(
            f"{self.run_id}|{item.operation_key}|turn:{turn}".encode()
        ).hexdigest()
        with self._state_lock:
            attempt = self.ledger.reserve(
                self.run_id, operation, active_attempts=frozenset(self._active_attempts)
            )
            self._active_attempts.add(attempt)
            self._attempt_items[attempt] = item.item_id
        return attempt

    def _finalize(self, params: _FinalizeParams) -> None:
        with self._state_lock:
            if params.attempt not in self._active_attempts:
                raise _Refused("unknown_attempt")
            item_id = self._attempt_items.pop(params.attempt, None)
            if item_id is not None:
                self._metrics.setdefault(item_id, ItemMetrics()).add(params.usage)
        try:
            counters = usage_counters(params.usage)
            model = params.model
            if self.config.is_zero_cost:
                self.ledger.finalize_priced(
                    params.attempt,
                    counters,
                    model=model,
                    known_usd=Decimal(0),
                    pricing_version=self.config.pricing_version or "subscription",
                )
            elif (
                params.outcome != "completed"
                and params.http_status is not None
                and 300 <= params.http_status <= 599
                and not (params.usage.output or params.usage.input)
            ):
                # Usage-less rejection envelope: known zero-cost, as before.
                self.ledger.finalize_error_envelope(params.attempt, params.http_status)
            elif params.outcome == "completed" and model == PRICED_MODEL:
                self.ledger.finalize(
                    params.attempt, counters, model=model, service_tier="default"
                )
            elif params.outcome == "completed" and params.cost_usd is not None:
                try:
                    cost = Decimal(repr(params.cost_usd))
                except InvalidOperation:
                    cost = None
                self.ledger.finalize_priced(
                    params.attempt,
                    counters,
                    model=model,
                    known_usd=cost,
                    pricing_version=PI_CATALOG_PRICING,
                )
            else:
                # Timeouts, transport loss and unpriced output stay unknown.
                self.ledger.finalize_priced(
                    params.attempt,
                    counters,
                    model=model,
                    known_usd=None,
                    pricing_version=PI_CATALOG_PRICING,
                )
        finally:
            with self._state_lock:
                self._active_attempts.discard(params.attempt)
