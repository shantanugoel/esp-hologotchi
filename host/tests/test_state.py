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
        self.assertIn("playfulness: 55/100", prompt)
        self.assertIn("recent_phrases: none", prompt)
        self.assertIn("choose self-directed actions sometimes", prompt)
        self.assertIn("including walk, play, excited, or nap", prompt)
        self.assertIn("Use happy/excited/play for direct affection", prompt)
        self.assertIn("Use worried for failed build/test results", prompt)
        self.assertIn("Use alert only for important alerts", prompt)
        self.assertIn("Avoid repeating any recent_phrases exactly", prompt)
        self.assertIn("Prefer duration_ms 2500-5000", prompt)

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
        self.assertEqual(state.recent_phrases, ("look now",))

    def test_recent_phrases_keep_last_five_text_outputs(self) -> None:
        state = PetState()

        for index in range(6):
            state.observe(
                BehaviorCommand(
                    mood="happy",
                    animation="happy",
                    text=f"phrase {index}",
                    alert=False,
                    duration_ms=2000,
                ),
                "quiet desk time",
            )

        self.assertEqual(
            state.recent_phrases,
            ("phrase 1", "phrase 2", "phrase 3", "phrase 4", "phrase 5"),
        )
        self.assertIn(
            "recent_phrases: phrase 1, phrase 2, phrase 3, phrase 4, phrase 5",
            state.prompt_context(),
        )

    def test_idle_event_reflects_current_state(self) -> None:
        sleepy = PetState(sleepiness=80)
        ignored = PetState(attention=10, sleepiness=30)

        self.assertIn("drowsy", sleepy.idle_event())
        self.assertIn("attention", ignored.idle_event())
