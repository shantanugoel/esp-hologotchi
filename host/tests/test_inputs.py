from __future__ import annotations

import unittest

from host.inputs import HostInputQueue, InputError


class HostInputQueueTests(unittest.TestCase):
    def test_submit_build_result_enqueues_compact_event(self) -> None:
        inputs = HostInputQueue()

        item = inputs.submit_build_test_result(
            "build",
            True,
            "  cargo   build completed  ",
        )

        self.assertEqual(item.id, "build-1")
        self.assertEqual(item.source, "build_result")
        self.assertEqual(item.event, "Build passed. cargo build completed")
        self.assertEqual(inputs.get_nowait(), item)

    def test_submit_test_failure_enqueues_failure_event(self) -> None:
        inputs = HostInputQueue()

        item = inputs.submit_build_test_result("test", False)

        self.assertEqual(item.id, "test-1")
        self.assertEqual(item.source, "test_result")
        self.assertEqual(item.event, "Test failed.")

    def test_submit_important_alert_enqueues_alert_event(self) -> None:
        inputs = HostInputQueue()

        item = inputs.submit_important_alert("  meeting starts now  ")

        self.assertEqual(item.id, "alert-1")
        self.assertEqual(item.source, "important_alert")
        self.assertEqual(item.event, "Important alert: meeting starts now")

    def test_build_result_rejects_invalid_kind(self) -> None:
        inputs = HostInputQueue()

        with self.assertRaisesRegex(InputError, "kind must be one of"):
            inputs.submit_build_test_result("deploy", True)

    def test_build_result_rejects_non_boolean_ok(self) -> None:
        inputs = HostInputQueue()

        with self.assertRaisesRegex(InputError, "ok must be a boolean"):
            inputs.submit_build_test_result("build", "yes")  # type: ignore[arg-type]

    def test_alert_rejects_empty_text(self) -> None:
        inputs = HostInputQueue()

        with self.assertRaisesRegex(InputError, "text must not be empty"):
            inputs.submit_important_alert("   ")


class TouchInputTests(unittest.TestCase):
    def test_submit_tap_enqueues_boop_event(self) -> None:
        inputs = HostInputQueue()

        item = inputs.submit_touch("tap")

        self.assertEqual(item.id, "touch-1")
        self.assertEqual(item.source, "touch")
        self.assertEqual(item.gesture, "tap")
        self.assertIn("boop", item.event)
        self.assertEqual(inputs.get_nowait(), item)

    def test_submit_hold_includes_duration_in_event(self) -> None:
        inputs = HostInputQueue()

        item = inputs.submit_touch("HOLD", 1200)

        self.assertEqual(item.gesture, "hold")
        self.assertIn("1200ms", item.event)

    def test_submit_hold_without_duration_is_allowed(self) -> None:
        inputs = HostInputQueue()

        item = inputs.submit_touch("hold")

        self.assertEqual(item.gesture, "hold")
        self.assertNotIn("ms", item.event)

    def test_submit_doubletap_is_a_play_invite(self) -> None:
        inputs = HostInputQueue()

        item = inputs.submit_touch("doubletap")

        self.assertEqual(item.gesture, "doubletap")
        self.assertIn("play", item.event)

    def test_touch_rejects_unknown_gesture(self) -> None:
        inputs = HostInputQueue()

        with self.assertRaisesRegex(InputError, "gesture must be one of"):
            inputs.submit_touch("swipe")

    def test_touch_rejects_negative_duration(self) -> None:
        inputs = HostInputQueue()

        with self.assertRaisesRegex(InputError, "duration_ms must not be negative"):
            inputs.submit_touch("hold", -5)

    def test_touch_rejects_non_finite_duration(self) -> None:
        inputs = HostInputQueue()

        with self.assertRaisesRegex(InputError, "finite"):
            inputs.submit_touch("hold", float("inf"))

    def test_touch_clamps_excessive_duration(self) -> None:
        inputs = HostInputQueue()

        item = inputs.submit_touch("hold", 10_000_000)

        self.assertIn("60000ms", item.event)


class PresenceSignalTests(unittest.TestCase):
    def test_presence_signal_is_enqueued(self) -> None:
        inputs = HostInputQueue()

        item = inputs.submit_presence_signal()

        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.source, "presence_signal")
        self.assertEqual(inputs.get_nowait(), item)

    def test_duplicate_presence_signals_coalesce_until_consumed(self) -> None:
        inputs = HostInputQueue()

        first = inputs.submit_presence_signal()
        second = inputs.submit_presence_signal()

        self.assertIsNotNone(first)
        self.assertIsNone(second)

        # Once the pending signal is consumed, a new one can be enqueued again.
        consumed = inputs.get_nowait()
        assert consumed is not None
        self.assertEqual(consumed.source, "presence_signal")
        self.assertIsNone(inputs.get_nowait())

        third = inputs.submit_presence_signal()
        self.assertIsNotNone(third)

    def test_presence_signal_does_not_block_other_inputs(self) -> None:
        inputs = HostInputQueue()
        inputs.submit_presence_signal()
        message = inputs.submit_direct_message("hi")

        first = inputs.get_nowait()
        second = inputs.get_nowait()

        assert first is not None and second is not None
        self.assertEqual({first.source, second.source}, {"presence_signal", "direct_message"})
        self.assertIn(message.id, {first.id, second.id})

