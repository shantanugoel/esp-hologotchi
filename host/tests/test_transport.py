from __future__ import annotations

import queue
import socket
import threading
import unittest
from unittest import mock

from host.protocol import BehaviorCommand
from host.transport import DeviceEndpoint, send_behavior


class _CaptureSocket:
    def __init__(self) -> None:
        self.sent = b""
        self.timeout: float | None = None

    def __enter__(self) -> "_CaptureSocket":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def sendall(self, payload: bytes) -> None:
        self.sent += payload


class TransportTests(unittest.TestCase):
    def test_send_behavior_writes_single_json_line(self) -> None:
        received: "queue.Queue[bytes]" = queue.Queue()
        ready = threading.Event()

        def serve_once() -> None:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
                server.bind(("127.0.0.1", 0))
                server.listen(1)
                port = server.getsockname()[1]
                received.put(str(port).encode("ascii"))
                ready.set()
                conn, _ = server.accept()
                with conn:
                    received.put(conn.recv(1024))

        thread = threading.Thread(target=serve_once, daemon=True)
        thread.start()
        ready.wait(timeout=2)

        port = int(received.get_nowait().decode("ascii"))
        send_behavior(
            BehaviorCommand(
                mood="sleepy",
                animation="sleepy",
                text="nap time",
                alert=False,
                duration_ms=3000,
            ),
            DeviceEndpoint(host="127.0.0.1", port=port, timeout_seconds=1.0),
        )

        payload = received.get(timeout=2).decode("ascii")
        thread.join(timeout=2)

        self.assertTrue(payload.endswith("\n"))
        self.assertIn('"animation":"sleepy"', payload)
        self.assertIn('"text":"nap time"', payload)

    def test_send_behavior_retries_after_connection_failures(self) -> None:
        attempts = 0
        fake_socket = _CaptureSocket()

        def flaky_connect(address: tuple[str, int], timeout: float) -> _CaptureSocket:
            nonlocal attempts
            self.assertEqual(address, ("mochi.local", 4242))
            self.assertEqual(timeout, 3.0)
            attempts += 1
            if attempts < 3:
                raise OSError("connection refused")
            return fake_socket

        with (
            mock.patch("host.transport.socket.create_connection", side_effect=flaky_connect),
            mock.patch("host.transport.time.sleep") as sleep,
        ):
            send_behavior(
                BehaviorCommand(
                    mood="alert",
                    animation="alert",
                    text="look now",
                    alert=True,
                    duration_ms=2000,
                ),
                DeviceEndpoint(
                    host="mochi.local",
                    port=4242,
                    connect_retries=4,
                    retry_delay_seconds=0.25,
                    timeout_seconds=3.0,
                ),
            )

        self.assertEqual(attempts, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertIn(b'"animation":"alert"', fake_socket.sent)
