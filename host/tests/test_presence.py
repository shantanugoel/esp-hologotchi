from __future__ import annotations

import threading
import unittest

from host.presence import (
    PresenceConfig,
    PresenceSignals,
    PresenceState,
    PresenceTracker,
    SignalMailbox,
)


def _tracker() -> PresenceTracker:
    return PresenceTracker(
        PresenceConfig(
            engaged_window_seconds=90.0,
            away_idle_seconds=300.0,
            return_min_seconds=120.0,
            focus_jealousy_seconds=1200.0,
        )
    )


class PresenceClassificationTests(unittest.TestCase):
    def test_recent_interaction_is_engaged(self) -> None:
        tracker = _tracker()
        tracker.note_interaction(now=1000.0)

        report = tracker.update(now=1030.0, signals=PresenceSignals())

        self.assertIs(report.state, PresenceState.ENGAGED)
        self.assertTrue(report.engaged)

    def test_no_signals_defaults_to_away(self) -> None:
        tracker = _tracker()

        report = tracker.update(now=5000.0, signals=PresenceSignals())

        self.assertIs(report.state, PresenceState.AWAY)

    def test_low_idle_time_is_present_but_ignoring(self) -> None:
        tracker = _tracker()

        report = tracker.update(now=5000.0, signals=PresenceSignals(idle_seconds=10.0))

        self.assertIs(report.state, PresenceState.PRESENT_IGNORING)

    def test_unlocked_screen_without_idle_is_present(self) -> None:
        tracker = _tracker()

        report = tracker.update(now=5000.0, signals=PresenceSignals(screen_locked=False))

        self.assertIs(report.state, PresenceState.PRESENT_IGNORING)

    def test_high_idle_time_is_away(self) -> None:
        tracker = _tracker()

        report = tracker.update(now=5000.0, signals=PresenceSignals(idle_seconds=600.0))

        self.assertIs(report.state, PresenceState.AWAY)

    def test_screen_locked_is_away(self) -> None:
        tracker = _tracker()

        report = tracker.update(now=5000.0, signals=PresenceSignals(screen_locked=True))

        self.assertIs(report.state, PresenceState.AWAY)

    def test_recent_interaction_outranks_idle_signal(self) -> None:
        tracker = _tracker()
        tracker.note_interaction(now=5000.0)

        report = tracker.update(now=5010.0, signals=PresenceSignals(idle_seconds=600.0))

        self.assertIs(report.state, PresenceState.ENGAGED)


class PresenceIgnoredTests(unittest.TestCase):
    def test_ignored_seconds_grow_while_present_but_ignoring(self) -> None:
        tracker = _tracker()

        first = tracker.update(now=1000.0, signals=PresenceSignals(idle_seconds=5.0))
        later = tracker.update(now=1300.0, signals=PresenceSignals(idle_seconds=5.0))

        self.assertEqual(first.ignored_seconds, 0.0)
        self.assertAlmostEqual(later.ignored_seconds, 300.0)

    def test_distinguishes_away_from_present_but_ignoring(self) -> None:
        tracker = _tracker()

        ignoring = tracker.update(now=1000.0, signals=PresenceSignals(idle_seconds=10.0))
        away = tracker.update(now=2000.0, signals=PresenceSignals(idle_seconds=900.0))

        self.assertIs(ignoring.state, PresenceState.PRESENT_IGNORING)
        self.assertIs(away.state, PresenceState.AWAY)


class PresenceReturnTests(unittest.TestCase):
    def test_returning_after_long_absence_flags_return(self) -> None:
        tracker = _tracker()
        tracker.update(now=1000.0, signals=PresenceSignals(idle_seconds=900.0))  # away

        report = tracker.update(now=1400.0, signals=PresenceSignals(idle_seconds=1.0))

        self.assertTrue(report.returned_from_away)
        self.assertAlmostEqual(report.away_before_return, 400.0)

    def test_brief_away_does_not_flag_return(self) -> None:
        tracker = _tracker()
        tracker.update(now=1000.0, signals=PresenceSignals(screen_locked=True))

        report = tracker.update(now=1030.0, signals=PresenceSignals(screen_locked=False))

        self.assertFalse(report.returned_from_away)


class PresenceFocusTests(unittest.TestCase):
    def test_long_single_app_focus_builds_jealousy_pressure(self) -> None:
        tracker = _tracker()

        tracker.update(now=0.0, signals=PresenceSignals(idle_seconds=5.0, foreground_app="editor"))
        report = tracker.update(
            now=2400.0, signals=PresenceSignals(idle_seconds=5.0, foreground_app="editor")
        )

        self.assertEqual(report.focus_app, "editor")
        self.assertGreater(report.focus_pressure, 0.0)

    def test_switching_apps_resets_focus(self) -> None:
        tracker = _tracker()

        tracker.update(now=0.0, signals=PresenceSignals(idle_seconds=5.0, foreground_app="editor"))
        report = tracker.update(
            now=1500.0, signals=PresenceSignals(idle_seconds=5.0, foreground_app="browser")
        )

        self.assertEqual(report.focus_app, "browser")
        self.assertEqual(report.focus_pressure, 0.0)

    def test_no_pressure_while_engaged(self) -> None:
        tracker = _tracker()
        tracker.update(now=0.0, signals=PresenceSignals(idle_seconds=5.0, foreground_app="editor"))
        tracker.note_interaction(now=3000.0)

        report = tracker.update(
            now=3010.0, signals=PresenceSignals(idle_seconds=5.0, foreground_app="editor")
        )

        self.assertIs(report.state, PresenceState.ENGAGED)
        self.assertEqual(report.focus_pressure, 0.0)


class SignalMailboxTests(unittest.TestCase):
    def test_latest_signal_wins(self) -> None:
        mailbox = SignalMailbox()
        self.assertTrue(mailbox.get().is_empty())

        mailbox.set(PresenceSignals(idle_seconds=12.0))
        mailbox.set(PresenceSignals(idle_seconds=34.0, foreground_app="term"))

        latest = mailbox.get()
        self.assertEqual(latest.idle_seconds, 34.0)
        self.assertEqual(latest.foreground_app, "term")

    def test_concurrent_access_is_safe(self) -> None:
        mailbox = SignalMailbox()

        def writer() -> None:
            for value in range(200):
                mailbox.set(PresenceSignals(idle_seconds=float(value)))

        threads = [threading.Thread(target=writer) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertIsNotNone(mailbox.get())


if __name__ == "__main__":
    unittest.main()
