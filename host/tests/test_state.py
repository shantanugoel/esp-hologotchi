from __future__ import annotations

import unittest

from host.affect import Affect
from host.presence import PresenceReport, PresenceState
from host.protocol import BehaviorCommand
from host.state import (
    MESSAGE_APOLOGY,
    MESSAGE_HARSH,
    MESSAGE_NEUTRAL,
    MESSAGE_PRAISE,
    PetState,
    build_stateful_prompt,
    classify_message,
    describe_self_directed_situation,
)


def _report(
    state: PresenceState,
    *,
    ignored_seconds: float = 0.0,
    away_seconds: float = 0.0,
    returned_from_away: bool = False,
    focus_app: str | None = None,
    focus_pressure: float = 0.0,
) -> PresenceReport:
    return PresenceReport(
        state=state,
        ignored_seconds=ignored_seconds,
        away_seconds=away_seconds,
        returned_from_away=returned_from_away,
        away_before_return=away_seconds,
        focus_app=focus_app,
        focus_seconds=0.0,
        focus_pressure=focus_pressure,
    )


class PetStatePromptTests(unittest.TestCase):
    def test_prompt_includes_needs_relationship_memories_and_presence(self) -> None:
        state = PetState(affect=Affect(social=30, loneliness=55, affection=80))

        prompt = build_stateful_prompt(
            state,
            "user is reading logs",
            presence=_report(PresenceState.PRESENT_IGNORING, ignored_seconds=200.0),
            memories=("Yesterday owner praised Mochi.",),
        )

        self.assertIn("Relevant memories:", prompt)
        self.assertIn("Yesterday owner praised Mochi.", prompt)
        self.assertIn("social: 30/100", prompt)
        self.assertIn("affection: 80/100", prompt)
        self.assertIn("Presence: present_but_ignoring", prompt)
        self.assertIn("user is reading logs", prompt)
        self.assertIn("Express feelings through the existing behaviors", prompt)
        self.assertIn("last_event: host loop started", prompt)

    def test_prompt_requires_non_empty_event(self) -> None:
        with self.assertRaises(ValueError):
            build_stateful_prompt(PetState(), "   ")

    def test_prompt_uses_configured_pet_name(self) -> None:
        prompt = build_stateful_prompt(PetState(), "quiet desk time", pet_name="Pip")

        self.assertIn("Choose Pip's next behavior", prompt)
        self.assertNotIn("Choose Mochi's next behavior", prompt)

    def test_recent_phrases_keep_last_five(self) -> None:
        state = PetState()
        for index in range(6):
            state.observe(
                BehaviorCommand(
                    mood="happy", animation="happy", text=f"phrase {index}",
                    alert=False, duration_ms=3000,
                ),
                "quiet desk time",
            )
        self.assertEqual(
            state.recent_phrases,
            ("phrase 1", "phrase 2", "phrase 3", "phrase 4", "phrase 5"),
        )

    def test_recent_animations_keep_last_five_and_enter_prompt(self) -> None:
        state = PetState()
        for animation in ("idle", "blink", "look_around", "walk", "happy", "play"):
            state.observe(
                BehaviorCommand(
                    mood="happy", animation=animation, text=None,
                    alert=False, duration_ms=3000,
                ),
                "quiet desk time",
            )

        self.assertEqual(
            state.recent_animations,
            ("blink", "look_around", "walk", "happy", "play"),
        )
        self.assertIn("Recent animations: blink, look_around", state.prompt_context())


class PetStateObserveTests(unittest.TestCase):
    def test_observe_records_mood_event_and_behavior_affect(self) -> None:
        state = PetState(affect=Affect(rest=10, sleepiness=90, energy=20))

        state.observe(
            BehaviorCommand(
                mood="sleepy", animation="nap", text=None, alert=False, duration_ms=8000
            ),
            "quiet desk time, drifting off",
        )

        self.assertEqual(state.mood, "sleepy")
        self.assertEqual(state.last_event, "quiet desk time, drifting off")
        self.assertGreater(state.affect.rest, 10)
        self.assertLess(state.affect.sleepiness, 90)


class SelfDirectedSituationTests(unittest.TestCase):
    def test_away_situation(self) -> None:
        state = PetState()
        text = describe_self_directed_situation(state, _report(PresenceState.AWAY))
        self.assertIn("away", text)

    def test_returned_situation(self) -> None:
        state = PetState()
        text = describe_self_directed_situation(
            state, _report(PresenceState.PRESENT_IGNORING, returned_from_away=True)
        )
        self.assertIn("came back", text)

    def test_lonely_situation_when_ignored(self) -> None:
        state = PetState(affect=Affect(social=10, loneliness=70, energy=60, frustration=10))
        text = describe_self_directed_situation(
            state, _report(PresenceState.PRESENT_IGNORING, ignored_seconds=900.0)
        )
        self.assertIn("lonely", text)

    def test_jealous_situation_with_focus_pressure(self) -> None:
        state = PetState(affect=Affect(social=45, loneliness=30, energy=60, play=60, stimulation=60))
        text = describe_self_directed_situation(
            state,
            _report(
                PresenceState.PRESENT_IGNORING,
                ignored_seconds=1500.0,
                focus_app="editor",
                focus_pressure=0.5,
            ),
        )
        self.assertIn("jealous", text)


class ClassifyMessageTests(unittest.TestCase):
    def test_praise(self) -> None:
        self.assertEqual(classify_message("good pup!"), MESSAGE_PRAISE)

    def test_harsh(self) -> None:
        self.assertEqual(classify_message("no, stop that"), MESSAGE_HARSH)

    def test_apology(self) -> None:
        self.assertEqual(classify_message("sorry buddy"), MESSAGE_APOLOGY)

    def test_neutral(self) -> None:
        self.assertEqual(classify_message("the deploy finished"), MESSAGE_NEUTRAL)

    def test_apology_outranks_other_words(self) -> None:
        self.assertEqual(classify_message("sorry that was bad"), MESSAGE_APOLOGY)


if __name__ == "__main__":
    unittest.main()
