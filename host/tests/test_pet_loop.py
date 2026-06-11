from __future__ import annotations

import io
import unittest

from host.ollama import OllamaConfig, OllamaError
from host.inputs import HostInputQueue
from host.pet_loop import PetLoopConfig, run_pet_loop
from host.protocol import BehaviorCommand


class PetLoopTests(unittest.TestCase):
    def test_loop_keeps_state_and_generates_repeated_behaviors(self) -> None:
        prompts: list[str] = []

        def fake_generate(prompt: str, config: OllamaConfig) -> BehaviorCommand:
            del config
            prompts.append(prompt)
            if len(prompts) == 1:
                return BehaviorCommand(
                    mood="happy",
                    animation="happy",
                    text="zoomies",
                    alert=False,
                    duration_ms=3000,
                )
            return BehaviorCommand(
                mood="calm",
                animation="idle",
                text=None,
                alert=False,
                duration_ms=3000,
            )

        sleeps: list[float] = []
        output = io.StringIO()

        state = run_pet_loop(
            PetLoopConfig(
                interval_seconds=0.25,
                max_cycles=2,
                initial_event="the build passed",
            ),
            OllamaConfig(timeout_seconds=1.0),
            endpoint=None,
            dry_run=True,
            generate=fake_generate,
            sleep=sleeps.append,
            output=output,
        )

        self.assertEqual(len(prompts), 2)
        self.assertEqual(sleeps, [0.25])
        self.assertIn("the build passed", prompts[0])
        self.assertIn("last_event: the build passed", prompts[1])
        self.assertEqual(state.mood, "calm")
        self.assertEqual(output.getvalue().count("\n"), 2)
        self.assertIn('"animation":"idle"', output.getvalue())
        self.assertIn('"duration_ms":1000', output.getvalue())

    def test_loop_continues_with_idle_fallback_after_model_error(self) -> None:
        prompts: list[str] = []

        def fake_generate(prompt: str, config: OllamaConfig) -> BehaviorCommand:
            del config
            prompts.append(prompt)
            if len(prompts) == 1:
                return BehaviorCommand(
                    mood="happy",
                    animation="happy",
                    text="zoomies",
                    alert=False,
                    duration_ms=3000,
                )
            raise OllamaError("timed out")

        output = io.StringIO()
        errors = io.StringIO()

        state = run_pet_loop(
            PetLoopConfig(
                interval_seconds=0.25,
                max_cycles=2,
                initial_event="quiet desk time",
            ),
            OllamaConfig(timeout_seconds=1.0),
            endpoint=None,
            dry_run=True,
            generate=fake_generate,
            sleep=lambda _: None,
            output=output,
            error_output=errors,
        )

        self.assertEqual(len(prompts), 2)
        self.assertEqual(output.getvalue().count("\n"), 2)
        self.assertIn('"animation":"idle"', output.getvalue())
        self.assertIn("model unavailable: timed out", errors.getvalue())
        self.assertEqual(state.mood, "calm")

    def test_loop_uses_queued_direct_message_before_next_idle_event(self) -> None:
        prompts: list[str] = []

        def fake_generate(prompt: str, config: OllamaConfig) -> BehaviorCommand:
            del config
            prompts.append(prompt)
            return BehaviorCommand(
                mood="happy",
                animation="happy",
                text="tail wag",
                alert=False,
                duration_ms=3000,
            )

        sleeps: list[float] = []
        inputs = HostInputQueue()
        inputs.submit_direct_message("Mochi, I fixed it")

        run_pet_loop(
            PetLoopConfig(
                interval_seconds=30.0,
                max_cycles=2,
                initial_event="quiet desk time",
            ),
            OllamaConfig(timeout_seconds=1.0),
            endpoint=None,
            dry_run=True,
            generate=fake_generate,
            sleep=sleeps.append,
            input_queue=inputs,
            output=io.StringIO(),
        )

        self.assertEqual(sleeps, [])
        self.assertIn("quiet desk time", prompts[0])
        self.assertIn("Direct user message: Mochi, I fixed it", prompts[1])

    def test_loop_logs_behavior_result_with_input_source_when_enabled(self) -> None:
        def fake_generate(prompt: str, config: OllamaConfig) -> BehaviorCommand:
            del prompt, config
            return BehaviorCommand(
                mood="happy",
                animation="happy",
                text="tail wag",
                alert=False,
                duration_ms=3000,
            )

        inputs = HostInputQueue()
        inputs.submit_direct_message("Mochi, I fixed it")
        errors = io.StringIO()

        run_pet_loop(
            PetLoopConfig(
                interval_seconds=30.0,
                max_cycles=2,
                initial_event="quiet desk time",
            ),
            OllamaConfig(timeout_seconds=1.0),
            endpoint=None,
            dry_run=True,
            generate=fake_generate,
            input_queue=inputs,
            log_events=True,
            output=io.StringIO(),
            error_output=errors,
        )

        logs = errors.getvalue()
        self.assertIn('"type":"behavior_result"', logs)
        self.assertIn('"input_id":"initial"', logs)
        self.assertIn('"input_id":"direct-1"', logs)
        self.assertIn('"source":"direct_message"', logs)
