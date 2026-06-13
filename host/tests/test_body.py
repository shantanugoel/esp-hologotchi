from __future__ import annotations

import unittest

from host.affect import Affect
from host.body import BodyConfig, BodyModel, BodyState
from host.presence import PresenceReport, PresenceState
from host.protocol import BehaviorCommand


def _config() -> BodyConfig:
    return BodyConfig(
        settle_seconds=10.0,
        drowsy_after_seconds=20.0,
        min_nap_seconds=30.0,
        waking_seconds=5.0,
    )


def _report(
    state: PresenceState,
    *,
    returned_from_away: bool = False,
    away_before_return: float = 0.0,
    ignored_seconds: float = 0.0,
) -> PresenceReport:
    return PresenceReport(
        state=state,
        ignored_seconds=ignored_seconds,
        away_seconds=0.0,
        returned_from_away=returned_from_away,
        away_before_return=away_before_return,
        focus_app=None,
        focus_seconds=0.0,
        focus_pressure=0.0,
    )


def _behavior(animation: str = "idle", *, alert: bool = False, text: str | None = None) -> BehaviorCommand:
    mood = "alert" if alert else "calm"
    return BehaviorCommand(
        mood=mood, animation=animation, text=text, alert=alert, duration_ms=4000
    )


def _awake_affect() -> Affect:
    return Affect(sleepiness=10.0, energy=80.0)


class BodyProgressionTests(unittest.TestCase):
    def test_away_progresses_awake_to_drowsy_to_sleeping(self) -> None:
        body = BodyModel(config=_config())
        away = _report(PresenceState.AWAY)

        first = body.advance(1000.0, affect=_awake_affect(), report=away, event_source="idle", local_hour=14)
        behavior, state = body.resolve(first, _behavior(), None, now=1000.0)
        self.assertIs(state, BodyState.AWAKE)
        self.assertIn(BodyState.DROWSY, first.allowed_states)

        # After the settle window, the deterministic default drifts to drowsy.
        second = body.advance(1015.0, affect=_awake_affect(), report=away, event_source="idle", local_hour=14)
        self.assertIs(second.default_state, BodyState.DROWSY)
        _, state = body.resolve(second, _behavior(), None, now=1015.0)
        self.assertIs(state, BodyState.DROWSY)

        # After drowsing long enough, Mochi falls asleep and the frame is clamped.
        third = body.advance(1040.0, affect=_awake_affect(), report=away, event_source="idle", local_hour=14)
        self.assertIs(third.default_state, BodyState.SLEEPING)
        behavior, state = body.resolve(third, _behavior("play"), None, now=1040.0)
        self.assertIs(state, BodyState.SLEEPING)
        self.assertEqual(behavior.animation, "nap")
        self.assertFalse(behavior.alert)


class BodyInertiaTests(unittest.TestCase):
    def test_sleep_inertia_blocks_wake_before_minimum_nap(self) -> None:
        body = BodyModel(
            state=BodyState.SLEEPING, state_since=1000.0, sleep_started_at=1000.0, config=_config()
        )
        situation = body.advance(
            1005.0, affect=_awake_affect(), report=_report(PresenceState.AWAY), event_source="idle", local_hour=14
        )
        self.assertNotIn(BodyState.AWAKE, situation.allowed_states)
        self.assertFalse(situation.min_nap_elapsed)

        # An idle proposal to wake is softened to the inertial default.
        _, state = body.resolve(situation, _behavior("excited"), "awake", now=1005.0)
        self.assertIs(state, BodyState.SLEEPING)

    def test_idle_tick_cannot_wake_before_minimum_nap_but_alert_can(self) -> None:
        body = BodyModel(
            state=BodyState.SLEEPING, state_since=1000.0, sleep_started_at=1000.0, config=_config()
        )
        idle = body.advance(
            1010.0, affect=_awake_affect(), report=_report(PresenceState.AWAY), event_source="idle", local_hour=14
        )
        self.assertNotIn(BodyState.WAKING, idle.allowed_states)

        alert = body.advance(
            1010.0,
            affect=_awake_affect(),
            report=_report(PresenceState.AWAY),
            event_source="important_alert",
            local_hour=14,
        )
        self.assertIn(BodyState.WAKING, alert.allowed_states)
        self.assertTrue(alert.wake_trigger)

    def test_minimum_nap_elapsed_allows_natural_wake_when_rested(self) -> None:
        body = BodyModel(
            state=BodyState.SLEEPING, state_since=1000.0, sleep_started_at=1000.0, config=_config()
        )
        situation = body.advance(
            1040.0,
            affect=_awake_affect(),
            report=_report(PresenceState.PRESENT_IGNORING),
            event_source="idle",
            local_hour=14,
        )
        self.assertTrue(situation.min_nap_elapsed)
        self.assertIs(situation.default_state, BodyState.WAKING)


class BodyWakeTests(unittest.TestCase):
    def test_returned_from_away_is_a_wake_trigger(self) -> None:
        body = BodyModel(
            state=BodyState.SLEEPING, state_since=1000.0, sleep_started_at=1000.0, config=_config()
        )
        situation = body.advance(
            1005.0,
            affect=_awake_affect(),
            report=_report(
                PresenceState.PRESENT_IGNORING, returned_from_away=True, away_before_return=2000.0
            ),
            event_source=PresenceState.PRESENT_IGNORING.value,
            local_hour=18,
        )
        self.assertTrue(situation.wake_trigger)
        self.assertIn(BodyState.WAKING, situation.allowed_states)
        behavior, state = body.resolve(situation, _behavior("happy"), "waking", now=1005.0)
        self.assertIs(state, BodyState.WAKING)

    def test_waking_auto_finishes_to_awake(self) -> None:
        body = BodyModel(state=BodyState.WAKING, state_since=1000.0, config=_config())
        situation = body.advance(
            1006.0, affect=_awake_affect(), report=_report(PresenceState.ENGAGED), event_source="idle", local_hour=14
        )
        self.assertIs(situation.state, BodyState.AWAKE)

    def test_alert_forces_awake_even_when_sleeping_proposed(self) -> None:
        body = BodyModel(
            state=BodyState.SLEEPING, state_since=1000.0, sleep_started_at=1000.0, config=_config()
        )
        situation = body.advance(
            1005.0,
            affect=_awake_affect(),
            report=_report(PresenceState.AWAY),
            event_source="important_alert",
            local_hour=2,
        )
        behavior, state = body.resolve(situation, _behavior("alert", alert=True), "sleeping", now=1005.0)
        self.assertIs(state, BodyState.AWAKE)
        self.assertEqual(behavior.animation, "alert")
        self.assertTrue(behavior.alert)


class BodyCoherenceTests(unittest.TestCase):
    def test_cannot_sleep_with_full_energy_and_no_reason(self) -> None:
        body = BodyModel(config=_config())
        situation = body.advance(
            1000.0,
            affect=Affect(sleepiness=0.0, energy=100.0),
            report=_report(PresenceState.PRESENT_IGNORING),
            event_source="idle",
            local_hour=14,
        )
        self.assertNotIn(BodyState.SLEEPING, situation.allowed_states)
        self.assertNotIn(BodyState.DROWSY, situation.allowed_states)
        _, state = body.resolve(situation, _behavior("nap"), "sleeping", now=1000.0)
        self.assertIs(state, BodyState.AWAKE)

    def test_fully_rested_sleeper_is_forced_awake_even_when_away(self) -> None:
        body = BodyModel(
            state=BodyState.SLEEPING, state_since=1000.0, sleep_started_at=1000.0, config=_config()
        )
        situation = body.advance(
            1005.0,
            affect=Affect(sleepiness=0.0, energy=100.0),
            report=_report(PresenceState.AWAY),
            event_source="idle",
            local_hour=14,
        )
        # No reason to sleep and fully rested: sleeping must not be an option.
        self.assertNotIn(BodyState.SLEEPING, situation.allowed_states)
        _, state = body.resolve(situation, _behavior("nap"), "sleeping", now=1005.0)
        self.assertIsNot(state, BodyState.SLEEPING)

    def test_late_night_keeps_rested_sleeper_asleep(self) -> None:
        body = BodyModel(
            state=BodyState.SLEEPING, state_since=1000.0, sleep_started_at=1000.0, config=_config()
        )
        situation = body.advance(
            1005.0,
            affect=Affect(sleepiness=0.0, energy=100.0),
            report=_report(PresenceState.AWAY),
            event_source="idle",
            local_hour=2,
        )
        # It is the middle of the night: dozing is coherent even when rested.
        self.assertIn(BodyState.SLEEPING, situation.allowed_states)

    def test_important_alert_offers_alert_animation(self) -> None:
        body = BodyModel(
            state=BodyState.SLEEPING, state_since=1000.0, sleep_started_at=1000.0, config=_config()
        )
        situation = body.advance(
            1005.0,
            affect=_awake_affect(),
            report=_report(PresenceState.AWAY),
            event_source="important_alert",
            local_hour=14,
        )
        self.assertIn("alert", situation.allowed_animations)

    def test_late_night_creates_sleep_pressure(self) -> None:
        body = BodyModel(config=_config())
        situation = body.advance(
            1000.0,
            affect=_awake_affect(),
            report=_report(PresenceState.PRESENT_IGNORING),
            event_source="idle",
            local_hour=23,
        )
        self.assertTrue(situation.sleep_pressure)
        self.assertTrue(situation.late_night)
        self.assertIn(BodyState.DROWSY, situation.allowed_states)

    def test_sleeping_allowed_animations_are_calm(self) -> None:
        body = BodyModel(
            state=BodyState.SLEEPING, state_since=1000.0, sleep_started_at=1000.0, config=_config()
        )
        situation = body.advance(
            1005.0, affect=_awake_affect(), report=_report(PresenceState.AWAY), event_source="idle", local_hour=14
        )
        self.assertIn("nap", situation.allowed_animations)
        self.assertNotIn("excited", situation.allowed_animations)
        self.assertNotIn("alert", situation.allowed_animations)

    def test_reunion_awake_offers_bright_animations(self) -> None:
        body = BodyModel(
            state=BodyState.SLEEPING, state_since=1000.0, sleep_started_at=1000.0, config=_config()
        )
        situation = body.advance(
            1005.0,
            affect=_awake_affect(),
            report=_report(
                PresenceState.PRESENT_IGNORING, returned_from_away=True, away_before_return=2000.0
            ),
            event_source=PresenceState.PRESENT_IGNORING.value,
            local_hour=18,
        )
        self.assertIn("happy", situation.allowed_animations)
        self.assertIn("nap", situation.allowed_animations)

    def test_clamp_keeps_drowsy_compatible_animation(self) -> None:
        body = BodyModel(state=BodyState.DROWSY, state_since=1000.0, config=_config())
        situation = body.advance(
            1005.0, affect=_awake_affect(), report=_report(PresenceState.AWAY), event_source="idle", local_hour=14
        )
        # "walk" is not drowsy-compatible -> softened to a calm default.
        behavior, state = body.resolve(situation, _behavior("walk"), "drowsy", now=1005.0)
        self.assertIs(state, BodyState.DROWSY)
        self.assertIn(behavior.animation, ("sleepy", "nap", "blink", "look_around", "idle"))

    def test_ongoing_sleep_downgrades_repeated_nap_to_gentle_beat(self) -> None:
        body = BodyModel(
            state=BodyState.SLEEPING, state_since=1000.0, sleep_started_at=1000.0, config=_config()
        )
        situation = body.advance(
            1005.0, affect=_awake_affect(), report=_report(PresenceState.AWAY), event_source="idle", local_hour=14
        )
        # Already asleep (not entering): a repeated deep "nap" is softened to a
        # gentle beat so affect does not over-recover every tick.
        behavior, state = body.resolve(situation, _behavior("nap"), "sleeping", now=1005.0)
        self.assertIs(state, BodyState.SLEEPING)
        self.assertEqual(behavior.animation, "sleepy")


class BodyPersistenceTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        body = BodyModel(
            state=BodyState.SLEEPING,
            state_since=1234.0,
            sleep_started_at=1230.0,
            last_touch_at=1200.0,
            config=_config(),
        )
        restored = BodyModel.from_row(body.to_row(), config=_config())
        self.assertIs(restored.state, BodyState.SLEEPING)
        self.assertEqual(restored.state_since, 1234.0)
        self.assertEqual(restored.sleep_started_at, 1230.0)
        self.assertEqual(restored.last_touch_at, 1200.0)

    def test_from_row_defaults_unknown_state_to_awake(self) -> None:
        restored = BodyModel.from_row({"state": "bogus"})
        self.assertIs(restored.state, BodyState.AWAKE)


if __name__ == "__main__":
    unittest.main()
