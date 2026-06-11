from __future__ import annotations

import io
import unittest

from host.ollama import OllamaConfig
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
