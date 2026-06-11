from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from host.ollama import OllamaConfig, generate_behavior


class _OllamaHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, object]] = []
    response_text = (
        '{"v":1,"kind":"behavior","mood":"happy","animation":"happy","text":"good build","alert":false,"duration_ms":4000}'
    )

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers["Content-Length"])
        body = self.rfile.read(length)
        type(self).requests.append(json.loads(body))

        payload = json.dumps({"response": type(self).response_text}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


class OllamaTests(unittest.TestCase):
    def setUp(self) -> None:
        _OllamaHandler.requests.clear()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _OllamaHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    def test_generate_behavior_sends_expected_prompt_shape(self) -> None:
        config = OllamaConfig(
            base_url=f"http://127.0.0.1:{self.server.server_address[1]}",
            model_family="qwen3.5",
            model_preset="qwen3.5:4b",
            timeout_seconds=2.0,
        )

        behavior = generate_behavior("the build just passed", config)

        self.assertEqual(behavior.animation, "happy")
        self.assertEqual(behavior.text, "good build")
        self.assertEqual(len(_OllamaHandler.requests), 1)

        request_body = _OllamaHandler.requests[0]
        self.assertEqual(request_body["model"], "qwen3.5:4b")
        self.assertEqual(request_body["format"], "json")
        self.assertFalse(request_body["stream"])
        self.assertIn("Mochi", request_body["system"])
        self.assertIn("the build just passed", request_body["prompt"])
