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

    def test_loop_extends_short_walk_behavior(self) -> None:
        def fake_generate(prompt: str, config: OllamaConfig) -> BehaviorCommand:
            del prompt, config
            return BehaviorCommand(
                mood="curious",
                animation="walk",
                text=None,
                alert=False,
                duration_ms=3000,
            )

        output = io.StringIO()

        run_pet_loop(
            PetLoopConfig(max_cycles=1, initial_event="quiet desk time"),
            OllamaConfig(timeout_seconds=1.0),
            endpoint=None,
            dry_run=True,
            generate=fake_generate,
            output=output,
        )

        self.assertIn('"animation":"walk"', output.getvalue())
        self.assertIn('"duration_ms":6500', output.getvalue())

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

    def test_loop_uses_queued_important_alert(self) -> None:
        prompts: list[str] = []

        def fake_generate(prompt: str, config: OllamaConfig) -> BehaviorCommand:
            del config
            prompts.append(prompt)
            return BehaviorCommand(
                mood="alert",
                animation="alert",
                text="look now",
                alert=True,
                duration_ms=3000,
            )

        inputs = HostInputQueue()
        inputs.submit_important_alert("calendar event starts now")
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

        self.assertIn("Important alert: calendar event starts now", prompts[1])
        self.assertIn('"input_id":"alert-1"', errors.getvalue())
        self.assertIn('"source":"important_alert"', errors.getvalue())

    def test_important_alert_falls_back_to_alert_when_model_output_is_invalid(self) -> None:
        calls = 0

        def fake_generate(prompt: str, config: OllamaConfig) -> BehaviorCommand:
            nonlocal calls
            del prompt, config
            calls += 1
            if calls == 1:
                return BehaviorCommand(
                    mood="calm",
                    animation="idle",
                    text=None,
                    alert=False,
                    duration_ms=3000,
                )
            raise OllamaError(
                "Ollama returned invalid behavior JSON: alert flag must match "
                "the alert animation exactly"
            )

        inputs = HostInputQueue()
        inputs.submit_important_alert("Mochi, gum gum")
        output = io.StringIO()
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
            output=output,
            error_output=errors,
        )

        payloads = output.getvalue()
        self.assertIn('"animation":"alert"', payloads)
        self.assertIn('"alert":true', payloads)
        self.assertIn('"text":"look now"', payloads)
        self.assertIn('"type":"loop_error"', errors.getvalue())


class SeqClock:
    def __init__(self, values: list[float]) -> None:
        self._values = list(values)

    def __call__(self) -> float:
        if len(self._values) > 1:
            return self._values.pop(0)
        return self._values[0]


def _idle_generator(prompts: list[str]):
    def fake_generate(prompt: str, config: object) -> BehaviorCommand:
        del config
        prompts.append(prompt)
        return BehaviorCommand(
            mood="calm", animation="idle", text=None, alert=False, duration_ms=3000
        )

    return fake_generate


class PetLoopPsychologyTests(unittest.TestCase):
    def test_being_ignored_grows_loneliness_over_real_time(self) -> None:
        from host.pet_loop import run_pet_loop as _run
        from host.state import PetState
        from host.affect import Affect
        from host.presence import PresenceSignals, SignalMailbox

        state = PetState(affect=Affect(social=20.0, loneliness=20.0))
        mailbox = SignalMailbox()
        mailbox.set(PresenceSignals(idle_seconds=5.0))  # present but not interacting

        result = _run(
            PetLoopConfig(interval_seconds=1.0, max_cycles=2, initial_event="quiet desk time"),
            OllamaConfig(timeout_seconds=1.0),
            endpoint=None,
            dry_run=True,
            state=state,
            generate=_idle_generator([]),
            sleep=lambda _: None,
            now=SeqClock([1000.0, 1000.0 + 600.0]),
            signal_mailbox=mailbox,
            output=io.StringIO(),
        )

        self.assertGreater(result.affect.loneliness, 20.0)

    def test_away_presence_does_not_grow_loneliness(self) -> None:
        from host.pet_loop import run_pet_loop as _run
        from host.state import PetState
        from host.affect import Affect
        from host.presence import PresenceSignals, SignalMailbox

        state = PetState(affect=Affect(social=20.0, loneliness=30.0))
        mailbox = SignalMailbox()
        mailbox.set(PresenceSignals(screen_locked=True))

        result = _run(
            PetLoopConfig(interval_seconds=1.0, max_cycles=2, initial_event="quiet desk time"),
            OllamaConfig(timeout_seconds=1.0),
            endpoint=None,
            dry_run=True,
            state=state,
            generate=_idle_generator([]),
            sleep=lambda _: None,
            now=SeqClock([1000.0, 1000.0 + 3600.0]),
            signal_mailbox=mailbox,
            output=io.StringIO(),
        )

        self.assertLessEqual(result.affect.loneliness, 30.0)

    def test_direct_praise_raises_affection(self) -> None:
        from host.pet_loop import run_pet_loop as _run
        from host.state import PetState

        inputs = HostInputQueue()
        inputs.submit_direct_message("good pup, nice work")

        result = _run(
            PetLoopConfig(interval_seconds=30.0, max_cycles=2, initial_event="quiet desk time"),
            OllamaConfig(timeout_seconds=1.0),
            endpoint=None,
            dry_run=True,
            state=PetState(),
            generate=_idle_generator([]),
            sleep=lambda _: None,
            now=SeqClock([1000.0, 1005.0]),
            input_queue=inputs,
            output=io.StringIO(),
        )

        self.assertGreater(result.affect.affection, 70.0)


class PetLoopMemoryTests(unittest.TestCase):
    def test_memory_is_captured_persisted_and_recalled(self) -> None:
        from host.memory import MemoryStore
        from host.pet_loop import run_pet_loop as _run
        from host.state import PetState

        clock = SeqClock([1000.0, 1005.0])
        memory = MemoryStore(now=clock)
        self.addCleanup(memory.close)

        inputs = HostInputQueue()
        inputs.submit_direct_message("good pup, you did great")

        _run(
            PetLoopConfig(interval_seconds=30.0, max_cycles=2, initial_event="quiet desk time"),
            OllamaConfig(timeout_seconds=1.0),
            endpoint=None,
            dry_run=True,
            state=PetState(),
            generate=_idle_generator([]),
            sleep=lambda _: None,
            now=clock,
            input_queue=inputs,
            memory=memory,
            output=io.StringIO(),
        )

        self.assertGreaterEqual(memory.count(), 1)

        # A fresh loop restores affect from memory and recalls the moment.
        prompts: list[str] = []
        restored = _run(
            PetLoopConfig(interval_seconds=30.0, max_cycles=1, initial_event="quiet desk time"),
            OllamaConfig(timeout_seconds=1.0),
            endpoint=None,
            dry_run=True,
            state=PetState(),
            generate=_idle_generator(prompts),
            sleep=lambda _: None,
            now=SeqClock([2000.0]),
            memory=memory,
            output=io.StringIO(),
        )

        self.assertIn("Relevant memories:", prompts[0])
        self.assertIn("good pup", prompts[0])
        self.assertGreater(restored.affect.affection, 70.0)


class IsFailureTests(unittest.TestCase):
    def test_pass_with_failed_in_detail_is_not_a_failure(self) -> None:
        from host.pet_loop import _is_failure

        self.assertFalse(_is_failure("Test passed. zero tests failed now"))
        self.assertTrue(_is_failure("Build failed. linker error"))
        self.assertTrue(_is_failure("Test failed."))
        self.assertFalse(_is_failure("Build passed."))


class PetLoopRestartTests(unittest.TestCase):
    def test_restart_after_long_absence_does_not_spike_loneliness(self) -> None:
        from host.memory import MemoryStore
        from host.pet_loop import run_pet_loop as _run
        from host.state import PetState
        from host.affect import Affect

        memory = MemoryStore(now=SeqClock([1000.0]))
        self.addCleanup(memory.close)
        # Persist a state from "last night" with a stale last_update.
        memory.save_affect(
            Affect(social=30.0, loneliness=25.0, last_update=1000.0).to_row(),
            last_interaction=1000.0,
        )

        # Restart 8 hours later with no presence signals (unknown -> benign away).
        result = _run(
            PetLoopConfig(interval_seconds=1.0, max_cycles=1, initial_event="quiet desk time"),
            OllamaConfig(timeout_seconds=1.0),
            endpoint=None,
            dry_run=True,
            state=PetState(),
            generate=_idle_generator([]),
            sleep=lambda _: None,
            now=SeqClock([1000.0 + 8 * 3600.0]),
            memory=memory,
            output=io.StringIO(),
        )

        self.assertLessEqual(result.affect.loneliness, 30.0)
