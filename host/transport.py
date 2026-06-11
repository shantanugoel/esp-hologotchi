from __future__ import annotations

import socket
import time
from dataclasses import dataclass

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
    if endpoint.connect_retries < 1:
        raise ValueError("connect_retries must be at least 1")

    payload = command.to_json_line().encode("ascii")
    last_error: OSError | None = None

    for attempt in range(endpoint.connect_retries):
        try:
            with socket.create_connection(
                (endpoint.host, endpoint.port), timeout=endpoint.timeout_seconds
            ) as sock:
                sock.settimeout(endpoint.timeout_seconds)
                sock.sendall(payload)
                return
        except OSError as exc:
            last_error = exc
            if attempt + 1 < endpoint.connect_retries:
                time.sleep(endpoint.retry_delay_seconds)

    raise TransportError(
        f"failed to send behavior to {endpoint.host}:{endpoint.port} after "
        f"{endpoint.connect_retries} attempt(s)"
    ) from last_error
