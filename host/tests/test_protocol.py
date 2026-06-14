from __future__ import annotations

import unittest

from host.protocol import (
    BehaviorCommand,
    ValidationError,
    parse_behavior_proposal,
    parse_behavior_response,
)


class ProtocolValidationTests(unittest.TestCase):
    def test_accepts_valid_behavior(self) -> None:
        behavior = parse_behavior_response(
            '{"v":1,"kind":"behavior","mood":"happy","animation":"happy","text":"zoomies!","alert":false,"duration_ms":5000}'
        )

        self.assertEqual(
            behavior,
            BehaviorCommand(
                mood="happy",
                animation="happy",
                text="zoomies!",
                alert=False,
                duration_ms=5000,
            ),
        )

    def test_accepts_expanded_pet_behavior(self) -> None:
        behavior = parse_behavior_response(
            '{"v":1,"kind":"behavior","mood":"happy","animation":"play","text":"play?","alert":false,"duration_ms":4500}'
        )

        self.assertEqual(behavior.mood, "happy")
        self.assertEqual(behavior.animation, "play")
        self.assertEqual(behavior.text, "play?")

    def test_accepts_confused_behavior(self) -> None:
        behavior = parse_behavior_response(
            '{"v":1,"kind":"behavior","mood":"curious","animation":"confused","text":"who you?","alert":false,"duration_ms":2500}'
        )

        self.assertEqual(behavior.mood, "curious")
        self.assertEqual(behavior.animation, "confused")
        self.assertEqual(behavior.text, "who you?")

    def test_accepts_behavior_wrapped_by_model_text(self) -> None:
        behavior = parse_behavior_response(
            '<think>Build status means Shiro should celebrate.</think>\n'
            '```json\n'
            '{"v":1,"kind":"behavior","mood":"happy","animation":"happy","text":"build passed","alert":false,"duration_ms":4000}\n'
            '```'
        )

        self.assertEqual(behavior.animation, "happy")
        self.assertEqual(behavior.text, "build passed")

    def test_canonicalizes_mood_animation_mismatch(self) -> None:
        behavior = parse_behavior_response(
            '{"v":1,"kind":"behavior","mood":"sleepy","animation":"blink","text":"zzz","alert":false,"duration_ms":3000}'
        )

        self.assertEqual(behavior.mood, "calm")
        self.assertEqual(behavior.animation, "blink")
        self.assertEqual(behavior.text, "zzz")

    def test_rejects_unknown_mood(self) -> None:
        with self.assertRaisesRegex(ValidationError, "unknown mood"):
            parse_behavior_response(
                '{"v":1,"kind":"behavior","mood":"drowsy","animation":"blink","text":"","alert":false,"duration_ms":3000}'
            )

    def test_rejects_non_ascii_text(self) -> None:
        with self.assertRaisesRegex(ValidationError, "ASCII"):
            parse_behavior_response(
                '{"v":1,"kind":"behavior","mood":"happy","animation":"happy","text":"olé","alert":false,"duration_ms":3000}'
            )

    def test_rejects_alert_flag_mismatch(self) -> None:
        with self.assertRaisesRegex(ValidationError, "alert flag"):
            parse_behavior_response(
                '{"v":1,"kind":"behavior","mood":"alert","animation":"alert","text":"look now","alert":false,"duration_ms":3000}'
            )

    def test_rejects_empty_model_output(self) -> None:
        with self.assertRaisesRegex(ValidationError, "empty"):
            parse_behavior_response("")

    def test_rejects_model_output_without_json_object(self) -> None:
        with self.assertRaisesRegex(ValidationError, "did not contain"):
            parse_behavior_response("I am happy about the build.")

    def test_strict_parser_rejects_proposal_fields(self) -> None:
        with self.assertRaisesRegex(ValidationError, "unexpected fields"):
            parse_behavior_response(
                '{"v":1,"kind":"behavior","mood":"sleepy","animation":"nap","text":"zzz",'
                '"alert":false,"duration_ms":9000,"intent":"stay_asleep","body_state":"sleeping"}'
            )


class ProposalParsingTests(unittest.TestCase):
    def test_accepts_optional_intent_and_body_state(self) -> None:
        proposal = parse_behavior_proposal(
            '{"v":1,"kind":"behavior","mood":"sleepy","animation":"nap","text":"zzz",'
            '"alert":false,"duration_ms":9000,"intent":"stay_asleep","body_state":"sleeping"}'
        )

        self.assertEqual(proposal.intent, "stay_asleep")
        self.assertEqual(proposal.body_state, "sleeping")
        self.assertEqual(
            proposal.to_behavior_command(),
            BehaviorCommand(
                mood="sleepy", animation="nap", text="zzz", alert=False, duration_ms=9000
            ),
        )

    def test_proposal_without_extras_has_none_fields(self) -> None:
        proposal = parse_behavior_proposal(
            '{"v":1,"kind":"behavior","mood":"happy","animation":"happy","text":"hi",'
            '"alert":false,"duration_ms":4000}'
        )

        self.assertIsNone(proposal.intent)
        self.assertIsNone(proposal.body_state)

    def test_rejects_unknown_intent(self) -> None:
        with self.assertRaisesRegex(ValidationError, "unknown intent"):
            parse_behavior_proposal(
                '{"v":1,"kind":"behavior","mood":"happy","animation":"happy","text":"hi",'
                '"alert":false,"duration_ms":4000,"intent":"do_a_backflip"}'
            )

    def test_softens_unknown_body_state_to_none(self) -> None:
        proposal = parse_behavior_proposal(
            '{"v":1,"kind":"behavior","mood":"happy","animation":"happy","text":"hi",'
            '"alert":false,"duration_ms":4000,"body_state":"hibernating"}'
        )

        self.assertIsNone(proposal.body_state)
        self.assertEqual(proposal.behavior.animation, "happy")

    def test_to_behavior_command_strips_host_only_fields(self) -> None:
        proposal = parse_behavior_proposal(
            '{"v":1,"kind":"behavior","mood":"happy","animation":"blink","text":"hi",'
            '"alert":false,"duration_ms":4000,"intent":"soft_reunion","body_state":"drowsy"}'
        )

        wire = proposal.to_behavior_command().to_json_line()
        self.assertNotIn("intent", wire)
        self.assertNotIn("body_state", wire)


if __name__ == "__main__":
    unittest.main()
