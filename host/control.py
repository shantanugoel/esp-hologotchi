from __future__ import annotations

import json
import sys
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import TracebackType
from typing import TextIO

from .inputs import HostInput, HostInputQueue, InputError

MAX_REQUEST_BYTES = 4096


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
        log_output: TextIO | None = None,
    ) -> None:
        self.config = config
        self._server = _build_server(config, inputs, log_output)
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
    log_output: TextIO | None = sys.stderr,
) -> ControlServer:
    server = ControlServer(config, inputs, log_output=log_output)
    server.start()
    return server


def _build_server(
    config: ControlServerConfig,
    inputs: HostInputQueue,
    log_output: TextIO | None,
) -> ThreadingHTTPServer:
    class Handler(_ControlHandler):
        input_queue = inputs
        control_log_output = log_output

    return ThreadingHTTPServer((config.bind_host, config.port), Handler)


class _ControlHandler(BaseHTTPRequestHandler):
    input_queue: HostInputQueue
    control_log_output: TextIO | None

    def do_GET(self) -> None:
        if self.path != "/health":
            self._send_error(HTTPStatus.NOT_FOUND, "not found")
            return

        self._send_json(HTTPStatus.OK, {"ok": True})

    def do_POST(self) -> None:
        if self.path not in {"/message", "/build", "/test", "/alert"}:
            self._send_error(HTTPStatus.NOT_FOUND, "not found")
            return

        try:
            payload = self._read_json_body()
            item = self._submit_input(self.path, payload)
        except InputError as exc:
            self._log(
                {
                    "type": "input",
                    "status": "rejected",
                    "source": "http",
                    "remote": self.client_address[0],
                    "error": str(exc),
                }
            )
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except ValueError as exc:
            self._log(
                {
                    "type": "input",
                    "status": "rejected",
                    "source": "http",
                    "remote": self.client_address[0],
                    "error": str(exc),
                }
            )
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return

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
        self._send_json(HTTPStatus.ACCEPTED, {"ok": True, "id": item.id})

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

        raise ValueError("unsupported input path")

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _read_json_body(self) -> dict[str, object]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("Content-Length is required")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("Content-Length must be an integer") from exc
        if length < 1:
            raise ValueError("request body must not be empty")
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

    def _log(self, payload: dict[str, object]) -> None:
        if self.control_log_output is None:
            return
        print(
            json.dumps(payload, separators=(",", ":"), ensure_ascii=True),
            file=self.control_log_output,
            flush=True,
        )
