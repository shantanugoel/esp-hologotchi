from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass
from types import TracebackType
from typing import Callable

from .inputs import TOUCH_GESTURES
from .protocol import BehaviorCommand

# Callback invoked for each valid uplink touch frame: (gesture, duration_ms).
TouchSink = Callable[[str, "int | None"], object]

# Largest uplink line the host will buffer before discarding it as malformed; a
# real device `input` frame is well under this.
MAX_UPLINK_LINE_BYTES = 256
_RECV_BYTES = 256
# How long the reader sleeps while there is no live socket to read from.
_READER_IDLE_SLEEP_SECONDS = 0.2
# Short, bounded connect timeout for the reader's background reconnect probe so a
# transient device disconnect restores touch uplink without waiting for the next
# behavior send.
_RECONNECT_PROBE_TIMEOUT_SECONDS = 1.0


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


def parse_input_frame(line: bytes | str) -> tuple[str, int | None] | None:
    """Parse one device -> host ``input`` uplink line.

    Returns ``(gesture, duration_ms)`` for a well-formed touch frame, or ``None``
    for anything malformed or unrecognized so the reader can simply ignore it
    (firmware may interleave keepalive newlines and partial writes).
    """

    if isinstance(line, bytes):
        try:
            line = line.decode("ascii")
        except UnicodeDecodeError:
            return None
    line = line.strip()
    if not line:
        return None
    try:
        payload = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("v") != 1 or payload.get("kind") != "input":
        return None
    if payload.get("source") != "touch":
        return None
    gesture = payload.get("gesture")
    if not isinstance(gesture, str) or gesture not in TOUCH_GESTURES:
        return None

    # Enforce the exact v1 contract: a hold always carries an integer
    # duration_ms; tap and doubletap never carry one.
    duration = payload.get("duration_ms")
    if gesture == "hold":
        if isinstance(duration, bool) or not isinstance(duration, int) or duration < 0:
            return None
    elif duration is not None:
        return None
    return gesture, duration


class BehaviorClient:
    """A persistent control connection to the device.

    Sends behavior frames (host -> device) and, when given an ``on_touch`` sink,
    runs a background reader that parses ``input`` uplink frames (device -> host)
    off the *same* socket. The socket reference is guarded by a lock so the
    sender, the keepalive, the reader, and reconnects never race on it; the
    blocking ``sendall``/``recv`` calls themselves run without holding the lock
    (a TCP socket is full-duplex, so concurrent send and receive are safe).
    """

    def __init__(
        self, endpoint: DeviceEndpoint, *, on_touch: TouchSink | None = None
    ) -> None:
        if endpoint.connect_retries < 1:
            raise ValueError("connect_retries must be at least 1")
        self.endpoint = endpoint
        self._on_touch = on_touch
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._closed = False
        self._ever_connected = False
        self._reader: threading.Thread | None = None
        if on_touch is not None:
            self._reader = threading.Thread(
                target=self._read_loop,
                name="hologotchi-uplink",
                daemon=True,
            )
            self._reader.start()

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
                with self._send_lock:
                    sock.sendall(payload)
                return
            except OSError as exc:
                last_error = exc
                self._drop(sock)

        raise TransportError(
            f"failed to send behavior to {self.endpoint.host}:{self.endpoint.port}"
        ) from last_error

    def send_keepalive(self) -> None:
        """Best-effort bare-newline keepalive to hold the device socket open.

        The device parses an empty line to ``ParseError::Empty`` and ignores it,
        so this is a no-op on the firmware side. It only exists to keep the TCP
        connection alive through long idle/nap waits that would otherwise exceed
        the device's inactivity timeout. Failures are swallowed; the next real
        ``send`` reconnects.
        """

        sock = self._current_sock()
        if sock is None:
            return
        try:
            with self._send_lock:
                sock.sendall(b"\n")
        except OSError:
            self._drop(sock)

    def close(self) -> None:
        self._closed = True
        sock = self._current_sock()
        with self._lock:
            self._sock = None
        if sock is not None:
            # Force-close so a reader blocked in recv wakes immediately.
            _safe_close(sock)
        if self._reader is not None:
            self._reader.join(timeout=2.0)

    # -- connection management ---------------------------------------------

    def _current_sock(self) -> socket.socket | None:
        with self._lock:
            return self._sock

    def _ensure_connected(self) -> socket.socket:
        with self._lock:
            if self._sock is not None:
                return self._sock

        sock = _connect(self.endpoint)
        with self._lock:
            if self._closed:
                _safe_close(sock)
                raise TransportError("client is closed")
            if self._sock is not None:
                # Another thread connected while we were blocked: keep theirs.
                _safe_close(sock)
                return self._sock
            self._sock = sock
            self._ever_connected = True
            return sock

    def _drop(self, sock: socket.socket) -> None:
        with self._lock:
            if self._sock is sock:
                self._sock = None
        _safe_close(sock)

    # -- uplink reader ------------------------------------------------------

    def _read_loop(self) -> None:
        buffer = b""
        attached: socket.socket | None = None
        while not self._closed:
            current = self._current_sock()
            if current is None:
                # Once the link has been up, re-establish it in the background so
                # touch uplink resumes promptly after a transient disconnect
                # without waiting for the loop's next behavior send. The loop
                # thread is never blocked by this.
                if self._ever_connected:
                    self._probe_reconnect()
                else:
                    time.sleep(_READER_IDLE_SLEEP_SECONDS)
                attached, buffer = None, b""
                continue
            if current is not attached:
                # (Re)attached to a freshly established socket after a reconnect.
                attached, buffer = current, b""
            try:
                data = current.recv(_RECV_BYTES)
            except socket.timeout:
                continue
            except OSError:
                self._drop(current)
                attached, buffer = None, b""
                continue
            if not data:
                # Remote closed the receive half.
                self._drop(current)
                attached, buffer = None, b""
                continue
            buffer = self._consume_lines(buffer + data)

    def _probe_reconnect(self) -> None:
        """Best-effort single reconnect attempt run from the reader thread.

        Bounded by a short connect timeout and a small backoff on failure so the
        reader neither blocks long nor busy-spins while the device is down. The
        sender may reconnect concurrently; the lock and the double-check keep the
        host to a single live socket.
        """

        timeout = min(self.endpoint.timeout_seconds, _RECONNECT_PROBE_TIMEOUT_SECONDS)
        try:
            sock = socket.create_connection(
                (self.endpoint.host, self.endpoint.port), timeout=timeout
            )
            sock.settimeout(self.endpoint.timeout_seconds)
        except OSError:
            time.sleep(_READER_IDLE_SLEEP_SECONDS)
            return
        with self._lock:
            if self._closed or self._sock is not None:
                _safe_close(sock)
                return
            self._sock = sock
            self._ever_connected = True

    def _consume_lines(self, buffer: bytes) -> bytes:
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            self._handle_line(line)
        if len(buffer) > MAX_UPLINK_LINE_BYTES:
            # An overlong line with no terminator: drop it rather than grow
            # unbounded. The next newline resynchronizes the stream.
            buffer = b""
        return buffer

    def _handle_line(self, raw: bytes) -> None:
        parsed = parse_input_frame(raw)
        if parsed is None:
            return
        gesture, duration_ms = parsed
        if self._on_touch is None:
            return
        try:
            self._on_touch(gesture, duration_ms)
        except Exception:
            # A sink error must never kill the reader thread.
            pass


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


def _safe_close(sock: socket.socket) -> None:
    try:
        close = getattr(sock, "close", None)
        if close is not None:
            close()
    except OSError:
        pass
