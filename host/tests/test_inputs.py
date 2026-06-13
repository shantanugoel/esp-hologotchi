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

