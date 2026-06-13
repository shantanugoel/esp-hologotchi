from __future__ import annotations

import queue
import socket
import threading
import unittest
from unittest import mock

from host.protocol import BehaviorCommand
from host.transport import BehaviorClient, DeviceEndpoint, send_behavior


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

    def test_behavior_client_reuses_connection_for_multiple_frames(self) -> None:
        received: "queue.Queue[bytes]" = queue.Queue()
        ready = threading.Event()

        def serve_once() -> None:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
                server.bind(("127.0.0.1", 0))
                server.listen(1)
                received.put(str(server.getsockname()[1]).encode("ascii"))
                ready.set()
                conn, _ = server.accept()
                with conn:
                    payload = b""
                    while payload.count(b"\n") < 2:
                        chunk = conn.recv(1024)
                        if not chunk:
                            break
                        payload += chunk
                    received.put(payload)

        thread = threading.Thread(target=serve_once, daemon=True)
        thread.start()
        ready.wait(timeout=2)

        port = int(received.get_nowait().decode("ascii"))
        endpoint = DeviceEndpoint(host="127.0.0.1", port=port, timeout_seconds=1.0)
        with BehaviorClient(endpoint) as client:
            client.send(
                BehaviorCommand(
                    mood="calm",
                    animation="idle",
                    text=None,
                    alert=False,
                    duration_ms=3000,
                )
            )
            client.send(
                BehaviorCommand(
                    mood="curious",
                    animation="look_around",
                    text="sniff?",
                    alert=False,
                    duration_ms=3000,
                )
            )

        payload = received.get(timeout=2).decode("ascii")
        thread.join(timeout=2)

        self.assertEqual(payload.count("\n"), 2)
        self.assertIn('"animation":"idle"', payload)
        self.assertIn('"animation":"look_around"', payload)


class KeepaliveTests(unittest.TestCase):
    def test_send_keepalive_writes_bare_newline(self) -> None:
        fake_socket = _CaptureSocket()

        with mock.patch(
            "host.transport.socket.create_connection", return_value=fake_socket
        ):
            client = BehaviorClient(DeviceEndpoint(host="mochi.local", timeout_seconds=1.0))
            client.send(
                BehaviorCommand(
                    mood="calm", animation="idle", text=None, alert=False, duration_ms=3000
                )
            )
            client.send_keepalive()

        self.assertTrue(fake_socket.sent.endswith(b"\n"))
        self.assertIn(b'"animation":"idle"', fake_socket.sent)
        self.assertTrue(fake_socket.sent.endswith(b"}\n\n"))

    def test_send_keepalive_is_noop_before_connecting(self) -> None:
        client = BehaviorClient(DeviceEndpoint(host="mochi.local", timeout_seconds=1.0))
        # No socket yet: must not raise or connect.
        client.send_keepalive()

    def test_send_keepalive_swallows_socket_errors(self) -> None:
        class _BrokenSocket(_CaptureSocket):
            def sendall(self, payload: bytes) -> None:
                raise OSError("broken pipe")

            def close(self) -> None:
                self.closed = True

        broken = _BrokenSocket()
        with mock.patch(
            "host.transport.socket.create_connection", return_value=broken
        ):
            client = BehaviorClient(DeviceEndpoint(host="mochi.local", timeout_seconds=1.0))
            client._sock = broken  # type: ignore[attr-defined]
            client.send_keepalive()  # must not raise


if __name__ == "__main__":
    unittest.main()
