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
