from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import TracebackType
from typing import TextIO

from .inputs import HostInput, HostInputQueue, InputError
from .memory import MemoryRecord, MemoryStore
from .presence import (
    DEFAULT_PRESENCE_TTL_SECONDS,
    MAX_TTL_SECONDS,
    PresenceSignals,
    SignalMailbox,
)

MAX_REQUEST_BYTES = 4096
FOREGROUND_APP_MAX_LEN = 64
PRESENCE_SOURCE_MAX_LEN = 32
INPUT_PATHS = frozenset({"/message", "/build", "/test", "/alert", "/touch"})


class FeatureDisabledError(RuntimeError):
    """Raised when a request targets a feature the server was not started with."""


@dataclass(frozen=True)
class ControlServerConfig:
    bind_host: str = "127.0.0.1"
    port: int = 8787


class ControlServer:
    def __init__(
        self,
        config: ControlServerConfig,
        inputs: HostInputQueue,
        *,
        memory: MemoryStore | None = None,
        signal_mailbox: SignalMailbox | None = None,
        log_output: TextIO | None = None,
    ) -> None:
        self.config = config
        self._server = _build_server(config, inputs, memory, signal_mailbox, log_output)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="hologotchi-control",
            daemon=True,
        )

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2.0)

    def __enter__(self) -> "ControlServer":
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type, exc, tb
        self.close()


def start_control_server(
    config: ControlServerConfig,
    inputs: HostInputQueue,
    *,
    memory: MemoryStore | None = None,
    signal_mailbox: SignalMailbox | None = None,
    log_output: TextIO | None = sys.stderr,
) -> ControlServer:
    server = ControlServer(
        config,
        inputs,
        memory=memory,
        signal_mailbox=signal_mailbox,
        log_output=log_output,
    )
    server.start()
    return server


def _build_server(
    config: ControlServerConfig,
    inputs: HostInputQueue,
    memory: MemoryStore | None,
    signal_mailbox: SignalMailbox | None,
    log_output: TextIO | None,
) -> ThreadingHTTPServer:
    class Handler(_ControlHandler):
        input_queue = inputs
        memory_store = memory
        mailbox = signal_mailbox
        control_log_output = log_output

    return ThreadingHTTPServer((config.bind_host, config.port), Handler)


class _ControlHandler(BaseHTTPRequestHandler):
    input_queue: HostInputQueue
    memory_store: MemoryStore | None
    mailbox: SignalMailbox | None
    control_log_output: TextIO | None

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(HTTPStatus.OK, {"ok": True})
            return
        if self.path == "/memory":
            try:
                self._send_json(HTTPStatus.OK, self._memory_summary())
            except FeatureDisabledError as exc:
                self._send_error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
            return
        self._send_error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:
        path = self.path
        handlers = {
            "/presence": self._handle_presence,
            "/memory/forget": self._handle_memory_forget,
            "/memory/reset": self._handle_memory_reset,
            "/memory/writes": self._handle_memory_writes,
        }
        if path not in INPUT_PATHS and path not in handlers:
            self._send_error(HTTPStatus.NOT_FOUND, "not found")
            return

        try:
            payload = self._read_json_body(required=path != "/memory/reset")
            if path in INPUT_PATHS:
                item = self._submit_input(path, payload)
                self._log_accepted(item)
                self._send_json(HTTPStatus.ACCEPTED, {"ok": True, "id": item.id})
            else:
                status, response = handlers[path](payload)
                self._send_json(status, response)
        except FeatureDisabledError as exc:
            self._send_error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
        except InputError as exc:
            self._log_rejected(str(exc))
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
        except ValueError as exc:
            self._log_rejected(str(exc))
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))

    # -- inputs -------------------------------------------------------------

    def _submit_input(self, path: str, payload: dict[str, object]) -> HostInput:
        if path == "/message":
            text = payload.get("text")
            if not isinstance(text, str):
                raise InputError("text must be a string")
            return self.input_queue.submit_direct_message(text)

        if path in {"/build", "/test"}:
            ok = payload.get("ok")
            if not isinstance(ok, bool):
                raise InputError("ok must be a boolean")
            text = payload.get("text")
            if text is not None and not isinstance(text, str):
                raise InputError("text must be a string")
            return self.input_queue.submit_build_test_result(
                path.removeprefix("/"),
                ok,
                text,
            )

        if path == "/alert":
            text = payload.get("text")
            if not isinstance(text, str):
                raise InputError("text must be a string")
            return self.input_queue.submit_important_alert(text)

        if path == "/touch":
            gesture = payload.get("gesture")
            if not isinstance(gesture, str):
                raise InputError("gesture must be a string")
            duration = payload.get("duration_ms")
            if duration is not None and (
                isinstance(duration, bool) or not isinstance(duration, (int, float))
            ):
                raise InputError("duration_ms must be a number")
            return self.input_queue.submit_touch(gesture, duration)

        raise ValueError("unsupported input path")

    # -- presence (9b) ------------------------------------------------------

    def _handle_presence(self, payload: dict[str, object]) -> tuple[HTTPStatus, dict[str, object]]:
        if self.mailbox is None:
            raise FeatureDisabledError("presence signals are not enabled")
        changed, fields = _apply_presence_payload(self.mailbox, payload, now=time.time())
        woke_loop = False
        if changed and self.input_queue is not None:
            woke_loop = self.input_queue.submit_presence_signal() is not None
        self._log(
            {
                "type": "presence",
                "status": "accepted",
                "remote": self.client_address[0],
                "woke_loop": woke_loop,
                **fields,
            }
        )
        return HTTPStatus.ACCEPTED, {"ok": True}

    # -- memory (9c) --------------------------------------------------------

    def _require_memory(self) -> MemoryStore:
        if self.memory_store is None:
            raise FeatureDisabledError("memory is not enabled")
        return self.memory_store

    def _memory_summary(self) -> dict[str, object]:
        memory = self._require_memory()
        summary = memory.summary()
        return {
            "ok": True,
            "writes_enabled": summary.writes_enabled,
            "total": summary.total,
            "by_kind": summary.by_kind,
            "by_source": summary.by_source,
            "top": [_record_json(record) for record in summary.top],
            "recent": [_record_json(record) for record in memory.recent(10)],
        }

    def _handle_memory_forget(
        self, payload: dict[str, object]
    ) -> tuple[HTTPStatus, dict[str, object]]:
        memory = self._require_memory()
        memory_id = payload.get("id")
        if memory_id is not None:
            if not isinstance(memory_id, int) or isinstance(memory_id, bool):
                raise ValueError("id must be an integer")
            removed = memory.forget(memory_id)
            return HTTPStatus.OK, {"ok": True, "removed": removed}

        tag = _optional_str(payload, "tag")
        source = _optional_str(payload, "source")
        before = _optional_number(payload, "before")
        after = _optional_number(payload, "after")
        if tag is None and source is None and before is None and after is None:
            raise ValueError("forget requires id, tag, source, before, or after")
        removed = memory.forget_by(tag=tag, source=source, before=before, after=after)
        return HTTPStatus.OK, {"ok": True, "removed": removed}

    def _handle_memory_reset(
        self, payload: dict[str, object]
    ) -> tuple[HTTPStatus, dict[str, object]]:
        del payload
        memory = self._require_memory()
        memory.reset()
        return HTTPStatus.OK, {"ok": True}

    def _handle_memory_writes(
        self, payload: dict[str, object]
    ) -> tuple[HTTPStatus, dict[str, object]]:
        memory = self._require_memory()
        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        memory.set_writes_enabled(enabled)
        return HTTPStatus.OK, {"ok": True, "writes_enabled": enabled}

    # -- plumbing -----------------------------------------------------------

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _read_json_body(self, *, required: bool = True) -> dict[str, object]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            if required:
                raise ValueError("Content-Length is required")
            return {}
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("Content-Length must be an integer") from exc
        if length < 1:
            if required:
                raise ValueError("request body must not be empty")
            return {}
        if length > MAX_REQUEST_BYTES:
            raise ValueError(f"request body must be at most {MAX_REQUEST_BYTES} bytes")

        raw_body = self.rfile.read(length)
        try:
            decoded = raw_body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("request body must be UTF-8") from exc

        try:
            payload = json.loads(decoded)
        except json.JSONDecodeError as exc:
            raise ValueError("request body must be a JSON object") from exc
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    def _send_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self._send_json(status, {"ok": False, "error": message})

    def _log_accepted(self, item: HostInput) -> None:
        self._log(
            {
                "type": "input",
                "status": "accepted",
                "id": item.id,
                "source": item.source,
                "transport": "http",
                "remote": self.client_address[0],
                "event": item.event,
            }
        )

    def _log_rejected(self, error: str) -> None:
        self._log(
            {
                "type": "input",
                "status": "rejected",
                "source": "http",
                "remote": self.client_address[0],
                "error": error,
            }
        )

    def _log(self, payload: dict[str, object]) -> None:
        if self.control_log_output is None:
            return
        print(
            json.dumps(payload, separators=(",", ":"), ensure_ascii=True),
            file=self.control_log_output,
            flush=True,
        )


def _apply_presence_payload(
    mailbox: SignalMailbox, payload: dict[str, object], *, now: float
) -> tuple[bool, dict[str, object]]:
    """Route a /presence post to the right source and report whether it matters.

    A payload carrying ``present`` is an explicit-presence source (keyed by
    ``source``); anything else is the host-activity source. The returned flag is
    ``True`` only when the fused coarse presence (away vs present) actually
    changed, so frequent host-activity posts that do not cross a transition do
    not spam the loop, while meaningful changes (presence flip, screen lock, idle
    crossing the away threshold) do wake it.
    """

    if "present" in payload:
        present = payload.get("present")
        if not isinstance(present, bool):
            raise ValueError("present must be a boolean")
        source = _optional_str(payload, "source")
        if source is None:
            raise ValueError("source is required for explicit presence")
        source = source[:PRESENCE_SOURCE_MAX_LEN]
        ttl = _presence_ttl(payload, default=DEFAULT_PRESENCE_TTL_SECONDS)
        changed = mailbox.set_presence(source, present, ttl_seconds=ttl, now=now)
        return changed, {"present": present, "source": source, "ttl_seconds": ttl}

    signals = _parse_presence_signals(payload)
    ttl = _presence_ttl(payload, default=None)
    changed = mailbox.set_activity(signals, ttl_seconds=ttl, now=now)
    return changed, {
        "idle_seconds": signals.idle_seconds,
        "screen_locked": signals.screen_locked,
        "foreground_app": signals.foreground_app,
    }


def _presence_ttl(payload: dict[str, object], *, default: float | None) -> float | None:
    ttl = _optional_number(payload, "ttl_seconds")
    if ttl is None:
        return default
    if ttl <= 0:
        raise ValueError("ttl_seconds must be positive")
    return min(ttl, MAX_TTL_SECONDS)


def _parse_presence_signals(payload: dict[str, object]) -> PresenceSignals:
    idle_seconds = _optional_number(payload, "idle_seconds")
    if idle_seconds is not None and idle_seconds < 0:
        raise ValueError("idle_seconds must not be negative")

    screen_locked = payload.get("screen_locked")
    if screen_locked is not None and not isinstance(screen_locked, bool):
        raise ValueError("screen_locked must be a boolean")

    foreground_app = _optional_str(payload, "foreground_app")
    if foreground_app is not None:
        foreground_app = foreground_app[:FOREGROUND_APP_MAX_LEN]

    return PresenceSignals(
        idle_seconds=idle_seconds,
        screen_locked=screen_locked,
        foreground_app=foreground_app,
    )


def _optional_str(payload: dict[str, object], field: str) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    cleaned = " ".join(value.split())
    return cleaned or None


def _optional_number(payload: dict[str, object], field: str) -> float | None:
    value = payload.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    return float(value)


def _record_json(record: MemoryRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "created_at": record.created_at,
        "source": record.source,
        "kind": record.kind,
        "summary": record.summary,
        "tags": list(record.tags),
        "valence": record.valence,
        "intensity": record.intensity,
        "importance": round(record.importance, 2),
        "recall_count": record.recall_count,
    }
