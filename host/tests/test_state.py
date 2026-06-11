from __future__ import annotations

import unittest

from host.protocol import BehaviorCommand
from host.state import PetState, build_stateful_prompt


class PetStateTests(unittest.TestCase):
    def test_stateful_prompt_includes_persistent_state_and_event(self) -> None:
        state = PetState(energy=40, attention=65, last_event="build passed")

        prompt = build_stateful_prompt(state, "user is reading logs")

        self.assertIn("energy: 40/100", prompt)
        self.assertIn("attention: 65/100", prompt)
        self.assertIn("last_event: build passed", prompt)
        self.assertIn("user is reading logs", prompt)
        self.assertIn("vary between idle, blink, and look_around", prompt)

    def test_observe_updates_and_clamps_state(self) -> None:
        state = PetState(energy=98, attention=97, sleepiness=3)

        state.observe(
            BehaviorCommand(
                mood="alert",
                animation="alert",
                text="look now",
                alert=True,
                duration_ms=2000,
            ),
            "important alert arrived",
        )

        self.assertEqual(state.mood, "alert")
        self.assertEqual(state.energy, 100)
        self.assertEqual(state.attention, 100)
        self.assertEqual(state.sleepiness, 0)
        self.assertEqual(state.last_event, "important alert arrived")

    def test_idle_event_reflects_current_state(self) -> None:
        sleepy = PetState(sleepiness=80)
        ignored = PetState(attention=10, sleepiness=30)

        self.assertIn("drowsy", sleepy.idle_event())
        self.assertIn("attention", ignored.idle_event())
