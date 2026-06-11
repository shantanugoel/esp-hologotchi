from __future__ import annotations

import unittest

from host.protocol import BehaviorCommand, ValidationError, parse_behavior_response


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

    def test_accepts_behavior_wrapped_by_model_text(self) -> None:
        behavior = parse_behavior_response(
            '<think>Build status means Mochi should celebrate.</think>\n'
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
