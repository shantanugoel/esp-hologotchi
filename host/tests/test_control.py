from __future__ import annotations

import io
import json
import unittest
from urllib import error, request

from host.control import ControlServer, ControlServerConfig
from host.inputs import HostInputQueue


class ControlServerTests(unittest.TestCase):
    def test_post_message_enqueues_direct_message(self) -> None:
        inputs = HostInputQueue()
        logs = io.StringIO()
        with ControlServer(
            ControlServerConfig(port=0), inputs, log_output=logs
        ) as server:
            status, body = _request_json(
                f"http://{server.address[0]}:{server.address[1]}/message",
                method="POST",
                payload={"text": "Mochi, the bug is fixed"},
            )

            item = inputs.wait(0.5)

        self.assertEqual(status, 202)
        self.assertEqual(body, {"ok": True, "id": "direct-1"})
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.id, "direct-1")
        self.assertEqual(item.source, "direct_message")
        self.assertEqual(item.event, "Direct user message: Mochi, the bug is fixed")
        self.assertIn('"status":"accepted"', logs.getvalue())
        self.assertIn('"id":"direct-1"', logs.getvalue())

    def test_health_endpoint(self) -> None:
        inputs = HostInputQueue()
        with ControlServer(ControlServerConfig(port=0), inputs) as server:
            status, body = _request_json(
                f"http://{server.address[0]}:{server.address[1]}/health",
                method="GET",
            )

        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True})

    def test_post_build_enqueues_build_result(self) -> None:
        inputs = HostInputQueue()
        logs = io.StringIO()
        with ControlServer(
            ControlServerConfig(port=0), inputs, log_output=logs
        ) as server:
            status, body = _request_json(
                f"http://{server.address[0]}:{server.address[1]}/build",
                method="POST",
                payload={"ok": True, "text": "cargo build completed"},
            )

            item = inputs.wait(0.5)

        self.assertEqual(status, 202)
        self.assertEqual(body, {"ok": True, "id": "build-1"})
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.source, "build_result")
        self.assertEqual(item.event, "Build passed. cargo build completed")
        self.assertIn('"source":"build_result"', logs.getvalue())

    def test_post_test_enqueues_test_result(self) -> None:
        inputs = HostInputQueue()
        with ControlServer(ControlServerConfig(port=0), inputs) as server:
            status, body = _request_json(
                f"http://{server.address[0]}:{server.address[1]}/test",
                method="POST",
                payload={"ok": False},
            )

            item = inputs.wait(0.5)

        self.assertEqual(status, 202)
        self.assertEqual(body, {"ok": True, "id": "test-1"})
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.source, "test_result")
        self.assertEqual(item.event, "Test failed.")

    def test_post_alert_enqueues_important_alert(self) -> None:
        inputs = HostInputQueue()
        with ControlServer(ControlServerConfig(port=0), inputs) as server:
            status, body = _request_json(
                f"http://{server.address[0]}:{server.address[1]}/alert",
                method="POST",
                payload={"text": "calendar event starts now"},
            )

            item = inputs.wait(0.5)

        self.assertEqual(status, 202)
        self.assertEqual(body, {"ok": True, "id": "alert-1"})
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.source, "important_alert")
        self.assertEqual(item.event, "Important alert: calendar event starts now")

    def test_post_touch_enqueues_touch_input(self) -> None:
        inputs = HostInputQueue()
        logs = io.StringIO()
        with ControlServer(
            ControlServerConfig(port=0), inputs, log_output=logs
        ) as server:
            status, body = _request_json(
                f"http://{server.address[0]}:{server.address[1]}/touch",
                method="POST",
                payload={"gesture": "hold", "duration_ms": 1200},
            )

            item = inputs.wait(0.5)

        self.assertEqual(status, 202)
        self.assertEqual(body, {"ok": True, "id": "touch-1"})
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.source, "touch")
        self.assertEqual(item.gesture, "hold")
        self.assertIn("1200ms", item.event)
        self.assertIn('"source":"touch"', logs.getvalue())

    def test_post_touch_rejects_unknown_gesture(self) -> None:
        inputs = HostInputQueue()
        with ControlServer(ControlServerConfig(port=0), inputs) as server:
            status, body = _request_json(
                f"http://{server.address[0]}:{server.address[1]}/touch",
                method="POST",
                payload={"gesture": "swipe"},
            )

        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertIn("gesture must be one of", body["error"])

    def test_post_touch_rejects_non_numeric_duration(self) -> None:
        inputs = HostInputQueue()
        with ControlServer(ControlServerConfig(port=0), inputs) as server:
            status, body = _request_json(
                f"http://{server.address[0]}:{server.address[1]}/touch",
                method="POST",
                payload={"gesture": "hold", "duration_ms": "long"},
            )

        self.assertEqual(status, 400)
        self.assertIn("duration_ms", body["error"])

    def test_post_message_rejects_empty_text(self) -> None:
        inputs = HostInputQueue()
        logs = io.StringIO()
        with ControlServer(
            ControlServerConfig(port=0), inputs, log_output=logs
        ) as server:
            status, body = _request_json(
                f"http://{server.address[0]}:{server.address[1]}/message",
                method="POST",
                payload={"text": "   "},
            )

        self.assertEqual(status, 400)
        self.assertEqual(body["ok"], False)
        self.assertIn("text must not be empty", body["error"])
        self.assertIn('"status":"rejected"', logs.getvalue())

    def test_post_build_rejects_missing_ok(self) -> None:
        inputs = HostInputQueue()
        with ControlServer(ControlServerConfig(port=0), inputs) as server:
            status, body = _request_json(
                f"http://{server.address[0]}:{server.address[1]}/build",
                method="POST",
                payload={"text": "cargo build completed"},
            )

        self.assertEqual(status, 400)
        self.assertEqual(body["ok"], False)
        self.assertIn("ok must be a boolean", body["error"])


def _request_json(
    url: str,
    *,
    method: str,
    payload: dict[str, object] | None = None,
) -> tuple[int, dict[str, object]]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with request.urlopen(req, timeout=2.0) as response:
            return response.status, json.loads(response.read())
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


class ControlPresenceMemoryTests(unittest.TestCase):
    def test_presence_endpoint_updates_mailbox(self) -> None:
        from host.presence import SignalMailbox

        inputs = HostInputQueue()
        mailbox = SignalMailbox()
        with ControlServer(
            ControlServerConfig(port=0), inputs, signal_mailbox=mailbox
        ) as server:
            status, body = _request_json(
                f"http://{server.address[0]}:{server.address[1]}/presence",
                method="POST",
                payload={"idle_seconds": 42.0, "screen_locked": False, "foreground_app": "editor"},
            )

        self.assertEqual(status, 202)
        self.assertTrue(body["ok"])
        signals = mailbox.get()
        self.assertEqual(signals.idle_seconds, 42.0)
        self.assertEqual(signals.foreground_app, "editor")

    def test_presence_endpoint_disabled_returns_503(self) -> None:
        inputs = HostInputQueue()
        with ControlServer(ControlServerConfig(port=0), inputs) as server:
            status, body = _request_json(
                f"http://{server.address[0]}:{server.address[1]}/presence",
                method="POST",
                payload={"idle_seconds": 10.0},
            )

        self.assertEqual(status, 503)
        self.assertFalse(body["ok"])

    def test_presence_endpoint_rejects_bad_idle(self) -> None:
        from host.presence import SignalMailbox

        inputs = HostInputQueue()
        with ControlServer(
            ControlServerConfig(port=0), inputs, signal_mailbox=SignalMailbox()
        ) as server:
            status, body = _request_json(
                f"http://{server.address[0]}:{server.address[1]}/presence",
                method="POST",
                payload={"idle_seconds": -5.0},
            )

        self.assertEqual(status, 400)
        self.assertIn("idle_seconds", body["error"])

    def test_memory_inspect_forget_and_reset(self) -> None:
        from host.memory import MemoryStore

        inputs = HostInputQueue()
        memory = MemoryStore()
        self.addCleanup(memory.close)
        memory_id = memory.capture(
            "alert", "calendar event soon", valence=50, intensity=80, alert=True,
            tags=["alert", "calendar"],
        )
        assert memory_id is not None

        with ControlServer(ControlServerConfig(port=0), inputs, memory=memory) as server:
            base = f"http://{server.address[0]}:{server.address[1]}"

            status, body = _request_json(f"{base}/memory", method="GET")
            self.assertEqual(status, 200)
            self.assertEqual(body["total"], 1)
            self.assertEqual(body["top"][0]["summary"], "calendar event soon")

            status, body = _request_json(
                f"{base}/memory/forget", method="POST", payload={"tag": "calendar"}
            )
            self.assertEqual(status, 200)
            self.assertEqual(body["removed"], 1)

            status, body = _request_json(f"{base}/memory/reset", method="POST", payload={})
            self.assertEqual(status, 200)

        self.assertEqual(memory.count(), 0)

    def test_memory_writes_toggle(self) -> None:
        from host.memory import MemoryStore

        inputs = HostInputQueue()
        memory = MemoryStore()
        self.addCleanup(memory.close)

        with ControlServer(ControlServerConfig(port=0), inputs, memory=memory) as server:
            status, body = _request_json(
                f"http://{server.address[0]}:{server.address[1]}/memory/writes",
                method="POST",
                payload={"enabled": False},
            )

        self.assertEqual(status, 200)
        self.assertFalse(body["writes_enabled"])
        self.assertFalse(memory.writes_enabled)

    def test_memory_endpoint_disabled_returns_503(self) -> None:
        inputs = HostInputQueue()
        with ControlServer(ControlServerConfig(port=0), inputs) as server:
            status, body = _request_json(
                f"http://{server.address[0]}:{server.address[1]}/memory", method="GET"
            )

        self.assertEqual(status, 503)
        self.assertFalse(body["ok"])


class ControlExplicitPresenceTests(unittest.TestCase):
    def test_accepts_explicit_presence_payload_and_wakes_loop(self) -> None:
        from host.presence import SignalMailbox

        inputs = HostInputQueue()
        mailbox = SignalMailbox()
        with ControlServer(
            ControlServerConfig(port=0), inputs, signal_mailbox=mailbox
        ) as server:
            status, body = _request_json(
                f"http://{server.address[0]}:{server.address[1]}/presence",
                method="POST",
                payload={"present": True, "source": "airpods", "ttl_seconds": 30},
            )

        self.assertEqual(status, 202)
        self.assertTrue(body["ok"])
        self.assertIs(mailbox.get().present, True)
        signal = inputs.wait(0.5)
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.source, "presence_signal")

    def test_repeated_presence_posts_coalesce_into_one_signal(self) -> None:
        from host.presence import SignalMailbox

        inputs = HostInputQueue()
        mailbox = SignalMailbox()
        base = None
        with ControlServer(
            ControlServerConfig(port=0), inputs, signal_mailbox=mailbox
        ) as server:
            base = f"http://{server.address[0]}:{server.address[1]}/presence"
            _request_json(base, method="POST", payload={"present": True, "source": "airpods"})
            _request_json(base, method="POST", payload={"present": True, "source": "airpods"})

        first = inputs.get_nowait()
        second = inputs.get_nowait()
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_activity_post_wakes_only_on_transition(self) -> None:
        from host.presence import SignalMailbox

        inputs = HostInputQueue()
        mailbox = SignalMailbox()
        with ControlServer(
            ControlServerConfig(port=0), inputs, signal_mailbox=mailbox
        ) as server:
            base = f"http://{server.address[0]}:{server.address[1]}/presence"
            # First post establishes presence (unknown -> present): a transition.
            _request_json(base, method="POST", payload={"idle_seconds": 12.0})
            # A second near-identical post is not a transition.
            _request_json(base, method="POST", payload={"idle_seconds": 14.0})

        first = inputs.get_nowait()
        second = inputs.get_nowait()
        self.assertIsNotNone(first)
        assert first is not None
        self.assertEqual(first.source, "presence_signal")
        self.assertIsNone(second)

    def test_rejects_explicit_presence_without_source(self) -> None:
        from host.presence import SignalMailbox

        inputs = HostInputQueue()
        with ControlServer(
            ControlServerConfig(port=0), inputs, signal_mailbox=SignalMailbox()
        ) as server:
            status, body = _request_json(
                f"http://{server.address[0]}:{server.address[1]}/presence",
                method="POST",
                payload={"present": False},
            )

        self.assertEqual(status, 400)
        self.assertIn("source", body["error"])

    def test_rejects_non_positive_ttl(self) -> None:
        from host.presence import SignalMailbox

        inputs = HostInputQueue()
        with ControlServer(
            ControlServerConfig(port=0), inputs, signal_mailbox=SignalMailbox()
        ) as server:
            status, body = _request_json(
                f"http://{server.address[0]}:{server.address[1]}/presence",
                method="POST",
                payload={"present": True, "source": "airpods", "ttl_seconds": 0},
            )

        self.assertEqual(status, 400)
        self.assertIn("ttl_seconds", body["error"])
