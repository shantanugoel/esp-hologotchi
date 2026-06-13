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


class PetLoopSurpriseTests(unittest.TestCase):
    def test_repeated_animation_and_phrase_are_rewritten_when_alternative_fits(self) -> None:
        from host.affect import Affect
        from host.pet_loop import run_pet_loop as _run
        from host.state import PetState

        prompts: list[str] = []

        def fake_generate(prompt: str, config: object) -> BehaviorCommand:
            del config
            prompts.append(prompt)
            return BehaviorCommand(
                mood="happy", animation="happy", text="again",
                alert=False, duration_ms=3000,
            )

        state = PetState(
            affect=Affect(
                social=80, play=80, stimulation=80, energy=80,
                loneliness=5, frustration=5,
            ),
            recent_phrases=("again",),
            recent_animations=("happy",),
        )
        output = io.StringIO()

        _run(
            PetLoopConfig(max_cycles=1, initial_event="quiet desk time"),
            OllamaConfig(timeout_seconds=1.0),
            endpoint=None,
            dry_run=True,
            state=state,
            generate=fake_generate,
            output=output,
        )

        payload = output.getvalue()
        self.assertIn("Recent animations: happy", prompts[0])
        self.assertNotIn('"animation":"happy"', payload)
        self.assertIn('"text":""', payload)

    def test_spontaneous_callback_adds_old_memory_to_idle_prompt(self) -> None:
        from host.memory import MemoryStore
        from host.pet_loop import run_pet_loop as _run
        from host.state import PetState

        class Clock:
            value = 1000.0

            def __call__(self) -> float:
                return self.value

        clock = Clock()
        memory = MemoryStore(now=clock)
        self.addCleanup(memory.close)
        memory.capture(
            "direct_message",
            "owner said good pup after a hard bug",
            valence=70,
            intensity=70,
            owner_initiated=True,
            tags=["praise"],
        )
        clock.value += 2 * 24 * 3600
        prompts: list[str] = []

        _run(
            PetLoopConfig(interval_seconds=1.0, max_cycles=2, initial_event="quiet desk time"),
            OllamaConfig(timeout_seconds=1.0),
            endpoint=None,
            dry_run=True,
            state=PetState(),
            generate=_idle_generator(prompts),
            sleep=lambda _: None,
            now=clock,
            memory=memory,
            output=io.StringIO(),
        )

        self.assertIn("Spontaneous callback memory", prompts[1])
        self.assertIn("good pup", prompts[1])

    def test_green_result_milestone_is_added_to_prompt(self) -> None:
        from host.pet_loop import run_pet_loop as _run
        from host.state import PetState

        inputs = HostInputQueue()
        inputs.submit_build_test_result("build", True)
        state = PetState(green_build_total=99)
        prompts: list[str] = []

        _run(
            PetLoopConfig(interval_seconds=30.0, max_cycles=2, initial_event="quiet desk time"),
            OllamaConfig(timeout_seconds=1.0),
            endpoint=None,
            dry_run=True,
            state=state,
            generate=_idle_generator(prompts),
            input_queue=inputs,
            output=io.StringIO(),
        )

        self.assertIn("100th green build/test result", prompts[1])

    def test_self_made_attention_nudge_is_added_when_ignored_for_hours(self) -> None:
        from host.pet_loop import run_pet_loop as _run
        from host.presence import PresenceConfig, PresenceSignals, PresenceTracker, SignalMailbox
        from host.state import PetState

        mailbox = SignalMailbox()
        mailbox.set(
            PresenceSignals(
                idle_seconds=5.0,
                screen_locked=False,
                foreground_app="editor",
            )
        )
        prompts: list[str] = []

        _run(
            PetLoopConfig(interval_seconds=1.0, max_cycles=2, initial_event="quiet desk time"),
            OllamaConfig(timeout_seconds=1.0),
            endpoint=None,
            dry_run=True,
            state=PetState(last_self_nudge_at=-10_000.0),
            generate=_idle_generator(prompts),
            sleep=lambda _: None,
            now=SeqClock([1000.0, 1000.0 + 3 * 3600.0]),
            signal_mailbox=mailbox,
            presence_tracker=PresenceTracker(
                PresenceConfig(engaged_window_seconds=1.0, away_idle_seconds=300.0)
            ),
            output=io.StringIO(),
        )

        self.assertIn("Self-made attention alert", prompts[1])

    def test_idle_nap_away_slows_next_wait(self) -> None:
        from host.pet_loop import run_pet_loop as _run

        calls = 0

        def fake_generate(prompt: str, config: object) -> BehaviorCommand:
            nonlocal calls
            del prompt, config
            calls += 1
            if calls == 1:
                return BehaviorCommand(
                    mood="calm", animation="idle", text=None,
                    alert=False, duration_ms=3000,
                )
            return BehaviorCommand(
                mood="sleepy", animation="nap", text=None,
                alert=False, duration_ms=8000,
            )

        sleeps: list[float] = []

        _run(
            PetLoopConfig(interval_seconds=10.0, max_cycles=3, initial_event="quiet desk time"),
            OllamaConfig(timeout_seconds=1.0),
            endpoint=None,
            dry_run=True,
            generate=fake_generate,
            sleep=sleeps.append,
            output=io.StringIO(),
        )

        self.assertEqual(sleeps, [10.0, 40.0])


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


class PetLoopV2aTests(unittest.TestCase):
    def test_next_event_chunks_long_wait_and_sends_keepalives(self) -> None:
        from host.pet_loop import _next_event

        sleeps: list[float] = []
        pings: list[int] = []

        result = _next_event(40.0, None, sleeps.append, lambda: pings.append(1))

        self.assertIsNone(result)
        self.assertEqual(sleeps, [15.0, 15.0, 10.0])
        self.assertEqual(len(pings), 2)

    def test_dry_run_wait_is_not_chunked(self) -> None:
        from host.pet_loop import _next_event

        sleeps: list[float] = []
        result = _next_event(40.0, None, sleeps.append)

        self.assertIsNone(result)
        self.assertEqual(sleeps, [40.0])

    def test_away_progression_reaches_sleep_over_ticks(self) -> None:
        from host.body import BodyModel, BodyState
        from host.pet_loop import run_pet_loop as _run
        from host.presence import SignalMailbox

        mailbox = SignalMailbox()
        mailbox.set_presence("airpods", False, now=0.0)
        body = BodyModel()
        output = io.StringIO()

        _run(
            PetLoopConfig(interval_seconds=30.0, max_cycles=3, initial_event="quiet desk time"),
            OllamaConfig(timeout_seconds=1.0),
            endpoint=None,
            dry_run=True,
            generate=_idle_generator([]),
            sleep=lambda _: None,
            now=SeqClock([1000.0, 1100.0, 1300.0]),
            signal_mailbox=mailbox,
            body=body,
            output=output,
        )

        self.assertIs(body.state, BodyState.SLEEPING)
        self.assertIn('"animation":"nap"', output.getvalue())

    def test_presence_return_signal_builds_reunion_text(self) -> None:
        from host.pet_loop import run_pet_loop as _run
        from host.presence import PresenceConfig, PresenceTracker, SignalMailbox

        mailbox = SignalMailbox()
        mailbox.set_presence("airpods", False, ttl_seconds=600.0, now=1000.0)
        inputs = HostInputQueue()
        prompts: list[str] = []
        flipped: list[bool] = []

        def fake_generate(prompt: str, config: object) -> BehaviorCommand:
            del config
            prompts.append(prompt)
            if not flipped:
                flipped.append(True)
                mailbox.set_presence("airpods", True, ttl_seconds=600.0, now=1000.0)
                inputs.submit_presence_signal()
            return BehaviorCommand(
                mood="calm", animation="idle", text=None, alert=False, duration_ms=3000
            )

        _run(
            PetLoopConfig(interval_seconds=30.0, max_cycles=2, initial_event="quiet desk time"),
            OllamaConfig(timeout_seconds=1.0),
            endpoint=None,
            dry_run=True,
            generate=fake_generate,
            sleep=lambda _: None,
            now=SeqClock([1000.0, 1200.0]),
            input_queue=inputs,
            signal_mailbox=mailbox,
            presence_tracker=PresenceTracker(PresenceConfig(return_min_seconds=120.0)),
            output=io.StringIO(),
        )

        self.assertIn("Presence changed: the owner returned", prompts[1])

    def test_body_state_restored_from_memory_and_clamps_behavior(self) -> None:
        from host.memory import MemoryStore
        from host.pet_loop import run_pet_loop as _run
        from host.presence import SignalMailbox

        memory = MemoryStore(now=SeqClock([1000.0]))
        self.addCleanup(memory.close)
        memory.save_body(
            {
                "state": "sleeping",
                "state_since": 900.0,
                "sleep_started_at": 900.0,
                "last_touch_at": 0.0,
            }
        )

        mailbox = SignalMailbox()
        mailbox.set_presence("airpods", False, now=0.0)
        prompts: list[str] = []

        def fake_generate(prompt: str, config: object) -> BehaviorCommand:
            del config
            prompts.append(prompt)
            return BehaviorCommand(
                mood="happy", animation="excited", text="yay", alert=False, duration_ms=4000
            )

        output = io.StringIO()
        _run(
            PetLoopConfig(interval_seconds=30.0, max_cycles=1, initial_event="quiet desk time"),
            OllamaConfig(timeout_seconds=1.0),
            endpoint=None,
            dry_run=True,
            generate=fake_generate,
            sleep=lambda _: None,
            now=SeqClock([1000.0]),
            signal_mailbox=mailbox,
            memory=memory,
            output=output,
        )

        self.assertIn("- state: sleeping", prompts[0])
        wire = output.getvalue()
        # Already asleep (restored, not entering): clamp to a gentle sleep beat,
        # never the proposed "excited".
        self.assertNotIn('"animation":"excited"', wire)
        self.assertTrue(
            '"animation":"sleepy"' in wire or '"animation":"nap"' in wire,
            wire,
        )
        stored = memory.load_affect().body
        assert stored is not None
        self.assertEqual(stored["state"], "sleeping")

    def test_loop_strips_proposal_fields_from_device_frame(self) -> None:
        from host.pet_loop import run_pet_loop as _run
        from host.protocol import BehaviorProposal

        def fake_generate(prompt: str, config: object) -> BehaviorProposal:
            del prompt, config
            return BehaviorProposal(
                behavior=BehaviorCommand(
                    mood="happy", animation="happy", text="yay", alert=False, duration_ms=4000
                ),
                intent="celebrate",
                body_state="awake",
            )

        output = io.StringIO()
        _run(
            PetLoopConfig(max_cycles=1, initial_event="good job"),
            OllamaConfig(timeout_seconds=1.0),
            endpoint=None,
            dry_run=True,
            generate=fake_generate,
            output=output,
        )

        wire = output.getvalue()
        self.assertNotIn("body_state", wire)
        self.assertNotIn("intent", wire)
        self.assertIn('"animation":"happy"', wire)

    def test_idle_wait_is_capped_to_next_presence_expiry(self) -> None:
        from host.pet_loop import run_pet_loop as _run
        from host.presence import SignalMailbox

        mailbox = SignalMailbox()
        # Away with an explicit source expiring 10s from now; the adaptive away
        # interval would otherwise be up to 60s.
        mailbox.set_presence("airpods", False, ttl_seconds=10.0, now=1000.0)
        sleeps: list[float] = []

        _run(
            PetLoopConfig(interval_seconds=30.0, max_cycles=2, initial_event="quiet desk time"),
            OllamaConfig(timeout_seconds=1.0),
            endpoint=None,
            dry_run=True,
            generate=_idle_generator([]),
            sleep=sleeps.append,
            now=SeqClock([1000.0, 1000.0]),
            signal_mailbox=mailbox,
            output=io.StringIO(),
        )

        self.assertTrue(sleeps)
        self.assertGreater(sleeps[0], 0.0)
        self.assertLessEqual(sleeps[0], 10.0)

    def test_burn_in_injects_micromotion_after_static_hold(self) -> None:
        from host.body import BodyState
        from host.pet_loop import _burn_in_guardrail
        from host.presence import PresenceReport, PresenceState
        from host.state import PetState

        nap = BehaviorCommand(
            mood="sleepy", animation="nap", text=None, alert=False, duration_ms=8000
        )
        report = PresenceReport(
            state=PresenceState.AWAY,
            ignored_seconds=0.0,
            away_seconds=600.0,
            returned_from_away=False,
            away_before_return=0.0,
            focus_app=None,
            focus_seconds=0.0,
            focus_pressure=0.0,
        )

        within = _burn_in_guardrail(
            nap,
            PetState(recent_animations=("nap",), last_micromotion_at=1000.0),
            BodyState.SLEEPING,
            report,
            1100.0,
        )
        self.assertEqual(within.animation, "nap")

        shifted = _burn_in_guardrail(
            nap,
            PetState(recent_animations=("nap",), last_micromotion_at=1000.0),
            BodyState.SLEEPING,
            report,
            1200.0,
        )
        self.assertEqual(shifted.animation, "blink")

    def test_alert_while_sleeping_wakes_and_alerts(self) -> None:
        from host.body import BodyModel, BodyState
        from host.pet_loop import run_pet_loop as _run
        from host.presence import SignalMailbox

        mailbox = SignalMailbox()
        mailbox.set_presence("airpods", False, now=0.0)
        body = BodyModel(state=BodyState.SLEEPING, state_since=900.0, sleep_started_at=900.0)
        inputs = HostInputQueue()
        inputs.submit_important_alert("meeting starts now")

        def fake_generate(prompt: str, config: object) -> BehaviorCommand:
            del prompt, config
            return BehaviorCommand(
                mood="alert", animation="alert", text="look now", alert=True, duration_ms=6000
            )

        output = io.StringIO()
        _run(
            PetLoopConfig(interval_seconds=30.0, max_cycles=2, initial_event="quiet desk time"),
            OllamaConfig(timeout_seconds=1.0),
            endpoint=None,
            dry_run=True,
            generate=fake_generate,
            sleep=lambda _: None,
            now=SeqClock([1000.0, 1005.0]),
            input_queue=inputs,
            signal_mailbox=mailbox,
            body=body,
            output=output,
        )

        self.assertIs(body.state, BodyState.AWAKE)
        self.assertIn('"animation":"alert"', output.getvalue())


class PetLoopTouchTests(unittest.TestCase):
    def test_touch_effect_dispatch_is_gesture_specific(self) -> None:
        from host.affect import Affect
        from host.pet_loop import _apply_touch_effects

        hold = Affect(affection=40.0, frustration=80.0)
        _apply_touch_effects(hold, "hold")
        self.assertGreater(hold.affection, 40)
        self.assertLess(hold.frustration, 80)

        lively = Affect(play=20.0, energy=80.0)
        _apply_touch_effects(lively, "doubletap")
        self.assertGreater(lively.play, 20)

        tired = Affect(play=20.0, social=20.0, energy=10.0)
        _apply_touch_effects(tired, "doubletap")
        # Too tired to accept the play invite: a light boop, not a play spike.
        self.assertLess(tired.play, 30)
        self.assertGreater(tired.social, 20)

        tap = Affect(play=20.0, social=20.0)
        _apply_touch_effects(tap, "tap")
        self.assertEqual(tap.play, 20.0)
        self.assertGreater(tap.social, 20)

    def test_touch_counts_as_engagement_and_applies_effect(self) -> None:
        from host.pet_loop import run_pet_loop as _run
        from host.presence import PresenceTracker
        from host.state import PetState
        from host.affect import Affect

        inputs = HostInputQueue()
        inputs.submit_touch("hold", 1200)
        tracker = PresenceTracker()

        result = _run(
            PetLoopConfig(interval_seconds=30.0, max_cycles=2, initial_event="quiet desk time"),
            OllamaConfig(timeout_seconds=1.0),
            endpoint=None,
            dry_run=True,
            state=PetState(affect=Affect(affection=40.0, frustration=70.0)),
            generate=_idle_generator([]),
            sleep=lambda _: None,
            now=SeqClock([1000.0, 1005.0]),
            input_queue=inputs,
            presence_tracker=tracker,
            output=io.StringIO(),
        )

        # A hold repairs affection and soothes frustration...
        self.assertGreater(result.affect.affection, 40.0)
        self.assertLess(result.affect.frustration, 70.0)
        # ...and resets the "ignored" timer to the moment of the touch.
        self.assertEqual(tracker.last_interaction, 1005.0)

    def test_touch_while_sleeping_wakes_through_waking_not_chaos(self) -> None:
        from host.body import BodyModel, BodyState
        from host.pet_loop import run_pet_loop as _run
        from host.presence import SignalMailbox

        mailbox = SignalMailbox()
        mailbox.set_presence("airpods", False, now=0.0)
        body = BodyModel(state=BodyState.SLEEPING, state_since=900.0, sleep_started_at=900.0)
        inputs = HostInputQueue()
        inputs.submit_touch("tap")

        def fake_generate(prompt: str, config: object) -> BehaviorCommand:
            del prompt, config
            return BehaviorCommand(
                mood="happy", animation="excited", text="yay", alert=False, duration_ms=4000
            )

        output = io.StringIO()
        _run(
            PetLoopConfig(interval_seconds=30.0, max_cycles=2, initial_event="quiet desk time"),
            OllamaConfig(timeout_seconds=1.0),
            endpoint=None,
            dry_run=True,
            generate=fake_generate,
            sleep=lambda _: None,
            now=SeqClock([1000.0, 1005.0]),
            input_queue=inputs,
            signal_mailbox=mailbox,
            body=body,
            output=output,
        )

        # Touch interrupts sleep, but through a gentle waking transition rather
        # than snapping straight to "excited".
        self.assertIs(body.state, BodyState.WAKING)
        self.assertEqual(body.last_touch_at, 1005.0)
        self.assertNotIn('"animation":"excited"', output.getvalue())

    def test_doubletap_play_invite_offers_play_when_engaged(self) -> None:
        from host.pet_loop import run_pet_loop as _run
        from host.state import PetState
        from host.affect import Affect

        inputs = HostInputQueue()
        inputs.submit_touch("doubletap")
        prompts: list[str] = []

        _run(
            PetLoopConfig(interval_seconds=30.0, max_cycles=2, initial_event="quiet desk time"),
            OllamaConfig(timeout_seconds=1.0),
            endpoint=None,
            dry_run=True,
            state=PetState(affect=Affect(energy=80.0, sleepiness=10.0)),
            generate=_idle_generator(prompts),
            sleep=lambda _: None,
            now=SeqClock([1000.0, 1005.0]),
            input_queue=inputs,
            output=io.StringIO(),
        )

        # The play-invite moment reaches the prompt and "play" is an allowed option.
        self.assertIn("play invite", prompts[1])
        body_block = prompts[1].split("Allowed animations:", 1)[1]
        self.assertIn("- play", body_block)

    def test_alert_then_hold_soothes(self) -> None:
        from host.pet_loop import run_pet_loop as _run
        from host.state import PetState
        from host.affect import Affect

        inputs = HostInputQueue()
        inputs.submit_important_alert("meeting starts now")
        inputs.submit_touch("hold", 1500)

        result = _run(
            PetLoopConfig(interval_seconds=30.0, max_cycles=3, initial_event="quiet desk time"),
            OllamaConfig(timeout_seconds=1.0),
            endpoint=None,
            dry_run=True,
            state=PetState(affect=Affect(affection=40.0, frustration=60.0)),
            generate=_idle_generator([]),
            sleep=lambda _: None,
            now=SeqClock([1000.0, 1005.0, 1010.0]),
            input_queue=inputs,
            output=io.StringIO(),
        )

        # Holding through the alert calms Mochi and repairs affection.
        self.assertLess(result.affect.frustration, 60.0)
        self.assertGreater(result.affect.affection, 40.0)

    def test_touch_captures_affection_memory(self) -> None:
        from host.memory import MemoryStore
        from host.pet_loop import run_pet_loop as _run
        from host.state import PetState

        clock = SeqClock([1000.0, 1005.0])
        memory = MemoryStore(now=clock)
        self.addCleanup(memory.close)

        inputs = HostInputQueue()
        inputs.submit_touch("hold", 1200)

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

        self.assertGreaterEqual(memory.count_by(tag="affection"), 1)
        self.assertGreaterEqual(memory.count_by(tag="touch"), 1)


    def test_touch_while_sleeping_softens_proposed_awake_excited(self) -> None:
        from host.body import BodyModel, BodyState
        from host.pet_loop import run_pet_loop as _run
        from host.presence import SignalMailbox
        from host.protocol import BehaviorProposal

        mailbox = SignalMailbox()
        mailbox.set_presence("airpods", False, now=0.0)
        body = BodyModel(state=BodyState.SLEEPING, state_since=900.0, sleep_started_at=900.0)
        inputs = HostInputQueue()
        inputs.submit_touch("tap")

        def fake_generate(prompt: str, config: object) -> BehaviorProposal:
            del prompt, config
            return BehaviorProposal(
                behavior=BehaviorCommand(
                    mood="happy", animation="excited", text="yay", alert=False, duration_ms=4000
                ),
                body_state="awake",
            )

        output = io.StringIO()
        _run(
            PetLoopConfig(interval_seconds=30.0, max_cycles=2, initial_event="quiet desk time"),
            OllamaConfig(timeout_seconds=1.0),
            endpoint=None,
            dry_run=True,
            generate=fake_generate,
            sleep=lambda _: None,
            now=SeqClock([1000.0, 1005.0]),
            input_queue=inputs,
            signal_mailbox=mailbox,
            body=body,
            output=output,
        )

        # Even though the model proposed awake + excited, a sleeping touch is
        # softened to a gentle waking beat.
        self.assertIs(body.state, BodyState.WAKING)
        self.assertNotIn('"animation":"excited"', output.getvalue())


if __name__ == "__main__":
    unittest.main()