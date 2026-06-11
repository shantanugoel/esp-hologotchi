from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from types import TracebackType

from .protocol import BehaviorCommand


class TransportError(RuntimeError):
    """Raised when the host cannot deliver a behavior to the device."""


@dataclass(frozen=True)
class DeviceEndpoint:
    host: str
    port: int = 4242
    connect_retries: int = 5
    retry_delay_seconds: float = 0.5
    timeout_seconds: float = 5.0


def send_behavior(command: BehaviorCommand, endpoint: DeviceEndpoint) -> None:
    with BehaviorClient(endpoint) as client:
        client.send(command)


class BehaviorClient:
    def __init__(self, endpoint: DeviceEndpoint) -> None:
        if endpoint.connect_retries < 1:
            raise ValueError("connect_retries must be at least 1")
        self.endpoint = endpoint
        self._sock: socket.socket | None = None

    def __enter__(self) -> "BehaviorClient":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type, exc, tb
        self.close()

    def send(self, command: BehaviorCommand) -> None:
        payload = command.to_json_line().encode("ascii")
        last_error: OSError | None = None

        for _ in range(2):
            sock = self._ensure_connected()
            try:
                sock.sendall(payload)
                return
            except OSError as exc:
                last_error = exc
                self.close()

        raise TransportError(
            f"failed to send behavior to {self.endpoint.host}:{self.endpoint.port}"
        ) from last_error

    def close(self) -> None:
        if self._sock is None:
            return
        try:
            close = getattr(self._sock, "close", None)
            if close is not None:
                close()
        finally:
            self._sock = None

    def _ensure_connected(self) -> socket.socket:
        if self._sock is not None:
            return self._sock

        self._sock = _connect(self.endpoint)
        return self._sock


def _connect(endpoint: DeviceEndpoint) -> socket.socket:
    last_error: OSError | None = None

    for attempt in range(endpoint.connect_retries):
        try:
            sock = socket.create_connection(
                (endpoint.host, endpoint.port), timeout=endpoint.timeout_seconds
            )
            sock.settimeout(endpoint.timeout_seconds)
            return sock
        except OSError as exc:
            last_error = exc
            if attempt + 1 < endpoint.connect_retries:
                time.sleep(endpoint.retry_delay_seconds)

    raise TransportError(
        f"failed to send behavior to {endpoint.host}:{endpoint.port} after "
        f"{endpoint.connect_retries} attempt(s)"
    ) from last_error
