from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from host.protocol import ANIMATION_TO_MOOD, MAX_DURATION_MS, parse_behavior_response
from host.tools.demo_driver import (
    SEQUENCES,
    PoseHolder,
    Step,
    build_command,
    load_sequence,
)


class _FakeClient:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, command) -> None:  # noqa: ANN001 - test double
        self.sent.append(command.animation)


class BuildCommandTests(unittest.TestCase):
    def test_every_builtin_sequence_frame_is_device_valid(self) -> None:
        for name, steps in SEQUENCES.items():
            for step in steps:
                command = build_command(step)
                # The firmware parser must accept the exact wire frame.
                parsed = parse_behavior_response(command.to_json_line())
                self.assertEqual(parsed.animation, step.animation, name)
                self.assertEqual(parsed.mood, ANIMATION_TO_MOOD[step.animation], name)

    def test_mood_is_derived_and_alert_flag_matches_animation(self) -> None:
        self.assertEqual(build_command(Step("happy", "yay")).mood, "happy")
        self.assertFalse(build_command(Step("happy")).alert)
        alert = build_command(Step("alert", "look now"))
        self.assertTrue(alert.alert)
        self.assertEqual(alert.mood, "alert")

    def test_duration_is_clamped_into_range(self) -> None:
        self.assertEqual(build_command(Step("idle", duration_ms=99_999)).duration_ms, MAX_DURATION_MS)
        self.assertEqual(build_command(Step("idle", duration_ms=1)).duration_ms, 1_000)

    def test_unknown_animation_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_command(Step("zoomies"))

    def test_overlong_or_non_ascii_text_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_command(Step("idle", "x" * 25))
        with self.assertRaises(ValueError):
            build_command(Step("idle", "smol \u00e9"))


class LoadSequenceTests(unittest.TestCase):
    def test_sequence_file_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "seq.json"
            path.write_text(
                json.dumps([{"animation": "happy", "text": "hi"}, {"animation": "nap"}]),
                encoding="utf-8",
            )
            args = _Args(sequence_file=str(path))
            steps = load_sequence(args)
        self.assertEqual([s.animation for s in steps], ["happy", "nap"])

    def test_sequence_file_rejects_unknown_field(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "seq.json"
            path.write_text(json.dumps([{"animation": "idle", "bogus": 1}]), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_sequence(_Args(sequence_file=str(path)))


class PoseHolderTests(unittest.TestCase):
    def test_set_pose_sends_immediately(self) -> None:
        client = _FakeClient()
        holder = PoseHolder(client, resend_interval=60.0)
        holder.set_pose(build_command(Step("happy", "hi")))
        holder.stop()
        self.assertEqual(client.sent, ["happy"])

    def test_dry_run_holder_never_sends(self) -> None:
        holder = PoseHolder(None, resend_interval=1.0)
        holder.start()  # no thread starts without a client
        holder.set_pose(build_command(Step("idle")))
        holder.stop()  # must not raise


class _Args:
    def __init__(self, *, sequence: str = "hero", sequence_file: str | None = None) -> None:
        self.sequence = sequence
        self.sequence_file = sequence_file


if __name__ == "__main__":
    unittest.main()
