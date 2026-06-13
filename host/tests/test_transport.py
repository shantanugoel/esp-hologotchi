from __future__ import annotations

import queue
import socket
import threading
import time
import unittest
from unittest import mock

from host.inputs import HostInputQueue
from host.protocol import BehaviorCommand
from host.transport import (
    BehaviorClient,
    DeviceEndpoint,
    parse_input_frame,
    send_behavior,
)


def _idle_behavior() -> BehaviorCommand:
    return BehaviorCommand(
        mood="calm", animation="idle", text=None, alert=False, duration_ms=3000
    )


class _FakeDevice:
    """A minimal stand-in for the device TCP server used by the uplink tests.

    Accepts connections in a background thread (so reconnects work), exposes the
    most recently accepted connection, and can push uplink bytes or drop the
    connection to simulate a device-side disconnect.
    """

    def __init__(self) -> None:
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(1)
        self._server.settimeout(0.2)
        self.port = self._server.getsockname()[1]
        self._conn: socket.socket | None = None
        self._lock = threading.Lock()
        self._accepted = threading.Event()
        self._running = True
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self) -> None:
        while self._running:
            try:
                conn, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with self._lock:
                self._conn = conn
            self._accepted.set()

    def wait_accepted(self, timeout: float = 2.0) -> None:
        if not self._accepted.wait(timeout):
            raise AssertionError("device never accepted a connection")

    def reset_accepted(self) -> None:
        self._accepted.clear()

    def send(self, data: bytes) -> None:
        with self._lock:
            conn = self._conn
        if conn is None:
            raise AssertionError("no active device connection")
        conn.sendall(data)

    def drop(self) -> None:
        with self._lock:
            conn = self._conn
            self._conn = None
        if conn is not None:
            conn.close()

    def close(self) -> None:
        self._running = False
        self.drop()
        try:
            self._server.close()
        except OSError:
            pass
        self._thread.join(timeout=2.0)


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


class InputFrameParsingTests(unittest.TestCase):
    def test_parses_tap(self) -> None:
        self.assertEqual(
            parse_input_frame(
                '{"v":1,"kind":"input","source":"touch","gesture":"tap"}'
            ),
            ("tap", None),
        )

    def test_parses_hold_with_duration(self) -> None:
        self.assertEqual(
            parse_input_frame(
                '{"v":1,"kind":"input","source":"touch","gesture":"hold","duration_ms":960}'
            ),
            ("hold", 960),
        )

    def test_parses_doubletap_from_bytes_with_newline(self) -> None:
        self.assertEqual(
            parse_input_frame(
                b'{"v":1,"kind":"input","source":"touch","gesture":"doubletap"}\n'
            ),
            ("doubletap", None),
        )

    def test_rejects_malformed_json(self) -> None:
        self.assertIsNone(parse_input_frame("not json at all"))

    def test_rejects_empty_line(self) -> None:
        self.assertIsNone(parse_input_frame("\n"))

    def test_rejects_wrong_kind(self) -> None:
        self.assertIsNone(
            parse_input_frame(
                '{"v":1,"kind":"behavior","source":"touch","gesture":"tap"}'
            )
        )

    def test_rejects_wrong_source(self) -> None:
        self.assertIsNone(
            parse_input_frame(
                '{"v":1,"kind":"input","source":"voice","gesture":"tap"}'
            )
        )

    def test_rejects_unknown_gesture(self) -> None:
        self.assertIsNone(
            parse_input_frame(
                '{"v":1,"kind":"input","source":"touch","gesture":"swipe"}'
            )
        )

    def test_rejects_wrong_version(self) -> None:
        self.assertIsNone(
            parse_input_frame(
                '{"v":2,"kind":"input","source":"touch","gesture":"tap"}'
            )
        )

    def test_rejects_negative_duration(self) -> None:
        self.assertIsNone(
            parse_input_frame(
                '{"v":1,"kind":"input","source":"touch","gesture":"hold","duration_ms":-5}'
            )
        )

    def test_rejects_hold_without_duration(self) -> None:
        self.assertIsNone(
            parse_input_frame(
                '{"v":1,"kind":"input","source":"touch","gesture":"hold"}'
            )
        )

    def test_rejects_tap_with_duration(self) -> None:
        self.assertIsNone(
            parse_input_frame(
                '{"v":1,"kind":"input","source":"touch","gesture":"tap","duration_ms":100}'
            )
        )

    def test_rejects_float_hold_duration(self) -> None:
        self.assertIsNone(
            parse_input_frame(
                '{"v":1,"kind":"input","source":"touch","gesture":"hold","duration_ms":960.5}'
            )
        )


class UplinkReaderTests(unittest.TestCase):
    def _endpoint(self, device: _FakeDevice) -> DeviceEndpoint:
        return DeviceEndpoint(
            host="127.0.0.1",
            port=device.port,
            connect_retries=5,
            retry_delay_seconds=0.05,
            timeout_seconds=1.0,
        )

    def test_reader_submits_touch_to_host_input_queue(self) -> None:
        device = _FakeDevice()
        self.addCleanup(device.close)
        input_queue = HostInputQueue()
        client = BehaviorClient(
            self._endpoint(device),
            on_touch=lambda gesture, duration: input_queue.submit_touch(
                gesture, duration
            ),
        )
        self.addCleanup(client.close)

        client.send(_idle_behavior())  # establishes the shared connection
        device.wait_accepted()
        device.send(b'{"v":1,"kind":"input","source":"touch","gesture":"tap"}\n')

        item = input_queue.wait(2.0)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.source, "touch")
        self.assertEqual(item.gesture, "tap")

    def test_reader_parses_multiple_frames_from_one_read(self) -> None:
        device = _FakeDevice()
        self.addCleanup(device.close)
        collector: "queue.Queue[tuple[str, int | None]]" = queue.Queue()
        client = BehaviorClient(
            self._endpoint(device),
            on_touch=lambda gesture, duration: collector.put((gesture, duration)),
        )
        self.addCleanup(client.close)

        client.send(_idle_behavior())
        device.wait_accepted()
        # Two newline-delimited frames delivered in a single write.
        device.send(
            b'{"v":1,"kind":"input","source":"touch","gesture":"tap"}\n'
            b'{"v":1,"kind":"input","source":"touch","gesture":"doubletap"}\n'
        )

        self.assertEqual(collector.get(timeout=2), ("tap", None))
        self.assertEqual(collector.get(timeout=2), ("doubletap", None))

    def test_reader_ignores_malformed_uplink_lines(self) -> None:
        device = _FakeDevice()
        self.addCleanup(device.close)
        collector: "queue.Queue[tuple[str, int | None]]" = queue.Queue()
        client = BehaviorClient(
            self._endpoint(device),
            on_touch=lambda gesture, duration: collector.put((gesture, duration)),
        )
        self.addCleanup(client.close)

        client.send(_idle_behavior())
        device.wait_accepted()
        # Garbage, a bare keepalive newline, and a wrong-version frame surround
        # exactly one valid hold frame.
        device.send(
            b"garbage not json\n"
            b"\n"
            b'{"v":2,"kind":"input","source":"touch","gesture":"tap"}\n'
            b'{"v":1,"kind":"input","source":"touch","gesture":"hold","duration_ms":800}\n'
        )

        self.assertEqual(collector.get(timeout=2), ("hold", 800))
        # Nothing else should have been delivered.
        with self.assertRaises(queue.Empty):
            collector.get(timeout=0.3)

    def test_sender_and_reader_survive_disconnect_and_reconnect(self) -> None:
        device = _FakeDevice()
        self.addCleanup(device.close)
        collector: "queue.Queue[tuple[str, int | None]]" = queue.Queue()
        client = BehaviorClient(
            self._endpoint(device),
            on_touch=lambda gesture, duration: collector.put((gesture, duration)),
        )
        self.addCleanup(client.close)

        client.send(_idle_behavior())
        device.wait_accepted()
        device.send(b'{"v":1,"kind":"input","source":"touch","gesture":"tap"}\n')
        self.assertEqual(collector.get(timeout=2), ("tap", None))

        # Simulate a device-side disconnect. The reader observes EOF and
        # re-establishes the link in the background.
        device.reset_accepted()
        device.drop()
        device.wait_accepted()

        # The sender keeps working on the reconnected socket...
        client.send(_idle_behavior())
        # ...and uplink resumes on the fresh connection.
        device.send(
            b'{"v":1,"kind":"input","source":"touch","gesture":"hold","duration_ms":900}\n'
        )
        self.assertEqual(collector.get(timeout=2), ("hold", 900))


class SendSerializationTests(unittest.TestCase):
    def test_concurrent_sends_and_keepalives_do_not_interleave(self) -> None:
        state_lock = threading.Lock()
        in_flight = {"n": 0}
        overlaps: list[bool] = []

        class _SerialSocket:
            def settimeout(self, timeout: float) -> None:
                del timeout

            def sendall(self, payload: bytes) -> None:
                del payload
                with state_lock:
                    in_flight["n"] += 1
                    if in_flight["n"] > 1:
                        overlaps.append(True)
                time.sleep(0.01)
                with state_lock:
                    in_flight["n"] -= 1

            def close(self) -> None:
                pass

        sock = _SerialSocket()
        with mock.patch(
            "host.transport.socket.create_connection", return_value=sock
        ):
            client = BehaviorClient(
                DeviceEndpoint(host="mochi.local", timeout_seconds=1.0)
            )
            client.send(_idle_behavior())  # establish the shared socket

            workers = [
                threading.Thread(target=lambda: client.send(_idle_behavior()))
                for _ in range(6)
            ] + [threading.Thread(target=client.send_keepalive) for _ in range(6)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()

        self.assertEqual(overlaps, [], "sendall calls must be serialized")


if __name__ == "__main__":
    unittest.main()
