from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from typing import Callable, TextIO

from .affect import Affect
from .inputs import HostInput, HostInputQueue
from .memory import KIND_EPISODIC, MemoryStore
from .ollama import OllamaConfig, OllamaError, generate_behavior
from .presence import PresenceReport, PresenceSignals, PresenceTracker, SignalMailbox
from .protocol import ANIMATION_TO_MOOD, BehaviorCommand
from .reflection import consolidate_memory
from .state import (
    MESSAGE_APOLOGY,
    MESSAGE_HARSH,
    MESSAGE_PRAISE,
    PetState,
    build_stateful_prompt,
    classify_message,
    describe_self_directed_situation,
)
from .transport import BehaviorClient, DeviceEndpoint, TransportError

GenerateBehavior = Callable[[str, OllamaConfig], BehaviorCommand]
SleepFn = Callable[[float], None]
Clock = Callable[[], float]

IDLE_CAPABLE_ANIMATIONS = frozenset({"idle", "blink", "look_around"})
IDLE_LOOP_DURATION_MS = 3000
WALK_MIN_DURATION_MS = 6500
# Sources whose event drives a deterministic affect update.
EFFECT_SOURCES = frozenset(
    {"direct_message", "build_result", "test_result", "important_alert"}
)
# Sources that count as the owner directly interacting with Mochi (resets the
# "ignored" timer). An alert being raised is not the same as the owner engaging.
ENGAGEMENT_SOURCES = frozenset({"direct_message"})
RETRIEVE_LIMIT = 3
CALLBACK_MIN_SECONDS = 6 * 3600.0
SELF_NUDGE_MIN_SECONDS = 30 * 60.0
SELF_ALERT_IGNORED_SECONDS = 2 * 3600.0
MILESTONE_TOTALS = frozenset({3, 10, 25, 50, 100})


@dataclass(frozen=True)
class PetLoopConfig:
    interval_seconds: float = 6.0
    max_cycles: int | None = None
    initial_event: str = "Quiet desk time. Nothing urgent is happening."

    def __post_init__(self) -> None:
        if self.interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        if self.max_cycles is not None and self.max_cycles < 1:
            raise ValueError("max_cycles must be at least 1 when set")
        if not self.initial_event.strip():
            raise ValueError("initial_event must not be empty")


@dataclass(frozen=True)
class _LoopEvent:
    id: str
    source: str
    event: str

    @classmethod
    def from_input(cls, item: HostInput) -> "_LoopEvent":
        return cls(id=item.id, source=item.source, event=item.event)


def run_pet_loop(
    loop_config: PetLoopConfig,
    model_config: OllamaConfig,
    endpoint: DeviceEndpoint | None,
    *,
    dry_run: bool = False,
    state: PetState | None = None,
    generate: GenerateBehavior = generate_behavior,
    sleep: SleepFn = time.sleep,
    now: Clock = time.time,
    input_queue: HostInputQueue | None = None,
    presence_tracker: PresenceTracker | None = None,
    signal_mailbox: SignalMailbox | None = None,
    memory: MemoryStore | None = None,
    log_events: bool = False,
    output: TextIO | None = None,
    error_output: TextIO | None = None,
) -> PetState:
    out = output or sys.stdout
    err = error_output or sys.stderr
    pet_state = state or PetState()
    presence = presence_tracker or PresenceTracker()
    _restore_persistent_state(memory, pet_state, presence)

    pending: _LoopEvent | None = _LoopEvent(
        id="initial", source="initial", event=loop_config.initial_event
    )
    cycles = 0

    client = None if dry_run else BehaviorClient(_require_endpoint(endpoint))
    try:
        while loop_config.max_cycles is None or cycles < loop_config.max_cycles:
            now_ts = now()
            signals = signal_mailbox.get() if signal_mailbox is not None else PresenceSignals()
            if pending is not None and pending.source in ENGAGEMENT_SOURCES:
                presence.note_interaction(now_ts)

            report = presence.update(now_ts, signals)
            pet_state.affect.advance(
                now_ts,
                ignoring=report.ignoring,
                away=report.away,
                focus_pressure=report.focus_pressure,
            )
            if report.returned_from_away:
                pet_state.affect.register_return_after_away(report.away_before_return)

            if pending is not None:
                event = pending
                situation = pending.event
                if pending.source in EFFECT_SOURCES:
                    _apply_owner_effects(pet_state.affect, pending)
            else:
                situation = describe_self_directed_situation(pet_state, report)
                event = _LoopEvent(id="idle", source="idle", event=situation)

            if memory is not None:
                _capture_memory(memory, event, report, pet_state.affect)
                consolidate_memory(memory)

            notes = _surprise_notes(pet_state, event, report, memory, now_ts)
            if notes:
                situation = f"{situation}\n" + "\n".join(notes)

            memories = _retrieve_memories(memory, situation)

            prompt = build_stateful_prompt(
                pet_state, situation, presence=report, memories=memories
            )
            try:
                behavior = generate(prompt, model_config)
            except OllamaError as exc:
                if log_events:
                    _log_loop_error(err, event, f"model unavailable: {exc}")
                else:
                    print(f"model unavailable: {exc}", file=err, flush=True)
                behavior = _fallback_behavior(event)
            behavior = _apply_novelty(behavior, pet_state, event)
            behavior = _adapt_loop_behavior(behavior, loop_config)

            if client is not None:
                try:
                    client.send(behavior)
                except TransportError as exc:
                    if log_events:
                        _log_loop_error(err, event, f"device unavailable: {exc}")
                    else:
                        print(f"device unavailable: {exc}", file=err, flush=True)

            pet_state.observe(behavior, situation)
            if memory is not None:
                memory.save_affect(pet_state.affect.to_row(), presence.last_interaction)

            print(behavior.to_json_line(), end="", file=out, flush=True)
            if log_events:
                _log_loop_behavior(err, event, behavior)

            cycles += 1
            if loop_config.max_cycles is not None and cycles >= loop_config.max_cycles:
                break

            wait_seconds = _adaptive_interval(loop_config, report, event, behavior)
            pending = _next_event(wait_seconds, input_queue, sleep)
    finally:
        if client is not None:
            client.close()

    return pet_state


def _restore_persistent_state(
    memory: MemoryStore | None, pet_state: PetState, presence: PresenceTracker
) -> None:
    if memory is None:
        return
    stored = memory.load_affect()
    if stored.affect:
        pet_state.affect = Affect.from_row(stored.affect)
    if stored.last_interaction is not None:
        presence.last_interaction = stored.last_interaction


def _apply_owner_effects(affect: Affect, event: _LoopEvent) -> None:
    if event.source == "direct_message":
        kind = classify_message(event.event)
        if kind == MESSAGE_PRAISE:
            affect.register_praise()
        elif kind == MESSAGE_HARSH:
            affect.register_harsh()
        elif kind == MESSAGE_APOLOGY:
            affect.register_apology()
        else:
            affect.register_attention()
    elif event.source in {"build_result", "test_result"}:
        if _is_failure(event.event):
            affect.register_bad_outcome()
        else:
            affect.register_good_outcome()
    elif event.source == "important_alert":
        affect.register_alert()


def _capture_memory(
    memory: MemoryStore, event: _LoopEvent, report: PresenceReport, affect: Affect
) -> None:
    signal = _memory_signal(event, report, affect)
    if signal is None:
        return
    valence, intensity, owner_initiated, alert, tags = signal
    memory.capture(
        event.source,
        event.event,
        kind=KIND_EPISODIC,
        tags=tags,
        valence=valence,
        intensity=intensity,
        owner_initiated=owner_initiated,
        alert=alert,
    )


def _memory_signal(
    event: _LoopEvent, report: PresenceReport, affect: Affect
) -> tuple[int, int, bool, bool, list[str]] | None:
    source = event.source
    if source == "direct_message":
        kind = classify_message(event.event)
        valence = {
            MESSAGE_PRAISE: 65,
            MESSAGE_HARSH: -55,
            MESSAGE_APOLOGY: 35,
        }.get(kind, 20)
        return valence, 55, True, False, ["message", kind]
    if source in {"build_result", "test_result"}:
        label = source.split("_", 1)[0]
        if _is_failure(event.event):
            return -60, 65, False, False, [label, "fail"]
        return 50, 55, False, False, [label, "pass"]
    if source == "important_alert":
        return 35, 85, False, True, ["alert"]
    if report.returned_from_away:
        return 45, 60, False, False, ["presence", "reunion"]

    # Self-directed/idle moments: only the emotionally notable ones are worth keeping.
    inner = affect.overall_state()
    if inner == "withdrawn":
        return -55, _as_intensity(affect.loneliness, affect.frustration), False, False, ["ignored", "withdrawn"]
    if inner == "sad":
        return -45, _as_intensity(affect.loneliness, affect.frustration), False, False, ["ignored", "lonely"]
    if inner == "grumpy":
        return -35, _as_intensity(affect.frustration, affect.frustration), False, False, ["grumpy"]
    return None


def _as_intensity(primary: float, secondary: float) -> int:
    return max(0, min(100, int(round(max(primary, secondary)))))


def _is_failure(event_text: str) -> bool:
    # Build/test events read "Build passed." / "Test failed. <detail>"; classify
    # from the status sentence so a detail string mentioning "failed" can't flip a
    # pass into a failure.
    head = event_text.split(".", 1)[0].strip().lower()
    return head.endswith("failed")


def _retrieve_memories(memory: MemoryStore | None, situation: str) -> tuple[str, ...]:
    if memory is None:
        return ()
    records = memory.retrieve(query=situation, limit=RETRIEVE_LIMIT)
    return tuple(record.summary for record in records)


def _surprise_notes(
    state: PetState,
    event: _LoopEvent,
    report: PresenceReport,
    memory: MemoryStore | None,
    now: float,
) -> list[str]:
    notes: list[str] = []
    special = _record_special_moment(state, event, now, memory)
    if special is not None:
        notes.append(f"Rare special moment: {special}")

    if event.source == "idle":
        nudge = _self_nudge_note(state, report, now)
        if nudge is not None:
            notes.append(nudge)

        if memory is not None and now - state.last_callback_at >= CALLBACK_MIN_SECONDS:
            callback = memory.spontaneous_callback(cooldown_seconds=0.0)
            if callback is not None:
                state.last_callback_at = now
                notes.append(
                    "Spontaneous callback memory: bring this up lightly if it fits: "
                    f"{callback.summary}"
                )
    return notes


def _record_special_moment(
    state: PetState,
    event: _LoopEvent,
    now: float,
    memory: MemoryStore | None,
) -> str | None:
    day_key = _local_day_key(now)
    if event.source == "direct_message":
        if state.last_interaction_day != day_key:
            state.last_interaction_day = day_key
            return "first direct interaction of the day; greet the owner like a small reunion."
        return None

    if event.source in {"build_result", "test_result"}:
        if _is_failure(event.event):
            state.failure_streak += 1
            state.green_build_streak = 0
            if state.failure_streak == 3:
                return "third failure in a row; be worried but bounded and recoverable."
            return None

        state.failure_streak = 0
        state.green_build_total += 1
        state.green_build_streak += 1
        total = _green_result_total(state, memory)
        if total in MILESTONE_TOTALS or total % 100 == 0:
            return f"{_ordinal(total)} green build/test result; celebrate an earned milestone."
        if state.green_build_streak == 5:
            return "five green results in a row; act proud and playful."
        return None

    if event.source == "idle":
        hour = _local_hour(now)
        if hour < 6:
            key = f"{day_key}:late"
            if state.last_daybeat_key != key:
                state.last_daybeat_key = key
                return "late-night desk beat; Mochi may get sleepy or gently dramatic."
        if hour >= 22:
            key = f"{day_key}:night"
            if state.last_daybeat_key != key:
                state.last_daybeat_key = key
                return "night desk beat; Mochi may wind down toward sleep."
    return None


def _self_nudge_note(state: PetState, report: PresenceReport, now: float) -> str | None:
    if now - state.last_self_nudge_at < SELF_NUDGE_MIN_SECONDS:
        return None
    affect = state.affect
    if report.ignoring and report.ignored_seconds >= SELF_ALERT_IGNORED_SECONDS:
        state.last_self_nudge_at = now
        return (
            "Self-made attention alert: the owner has been heads-down for about "
            "two hours. Mochi may ask for attention once, gently, without guilt."
        )
    if report.away:
        return None
    if affect.social < 25 or affect.loneliness >= 55:
        state.last_self_nudge_at = now
        return "Self-initiated nudge: Mochi may ask for a tiny bit of attention."
    if affect.play < 25 or affect.stimulation < 25:
        state.last_self_nudge_at = now
        return "Self-initiated nudge: Mochi may start a tiny game or patrol."
    return None


def _apply_novelty(
    behavior: BehaviorCommand, state: PetState, event: _LoopEvent
) -> BehaviorCommand:
    text = behavior.text
    if text is not None and text in state.recent_phrases:
        text = None

    if (
        behavior.alert
        or event.source == "important_alert"
        or behavior.animation not in state.recent_animations
    ):
        if text == behavior.text:
            return behavior
        return BehaviorCommand(
            mood=behavior.mood,
            animation=behavior.animation,
            text=text,
            alert=behavior.alert,
            duration_ms=behavior.duration_ms,
        )

    for animation in state.affect.suggested_animations():
        if animation == "alert" or animation in state.recent_animations:
            continue
        if animation in IDLE_CAPABLE_ANIMATIONS:
            text = None
        return BehaviorCommand(
            mood=ANIMATION_TO_MOOD[animation],
            animation=animation,
            text=text,
            alert=False,
            duration_ms=behavior.duration_ms,
        )

    if text == behavior.text:
        return behavior
    return BehaviorCommand(
        mood=behavior.mood,
        animation=behavior.animation,
        text=text,
        alert=behavior.alert,
        duration_ms=behavior.duration_ms,
    )


def _green_result_total(state: PetState, memory: MemoryStore | None) -> int:
    if memory is None:
        return state.green_build_total
    return max(state.green_build_total, memory.count_by(tag="pass"))


def _ordinal(value: int) -> str:
    suffix = "th"
    if value % 100 not in {11, 12, 13}:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


def _require_endpoint(endpoint: DeviceEndpoint | None) -> DeviceEndpoint:
    if endpoint is None:
        raise ValueError("endpoint is required unless dry_run is enabled")
    return endpoint


def _fallback_behavior(event: _LoopEvent) -> BehaviorCommand:
    if event.source == "important_alert":
        return BehaviorCommand(
            mood="alert",
            animation="alert",
            text="look now",
            alert=True,
            duration_ms=6500,
        )

    return BehaviorCommand(
        mood="calm",
        animation="idle",
        text=None,
        alert=False,
        duration_ms=5000,
    )


def _next_event(
    wait_seconds: float,
    input_queue: HostInputQueue | None,
    sleep: SleepFn,
) -> _LoopEvent | None:
    if input_queue is None:
        sleep(wait_seconds)
        return None

    item = input_queue.get_nowait()
    if item is not None:
        return _LoopEvent.from_input(item)
    item = input_queue.wait(wait_seconds)
    if item is not None:
        return _LoopEvent.from_input(item)
    return None


def _adaptive_interval(
    loop_config: PetLoopConfig,
    report: PresenceReport,
    event: _LoopEvent,
    behavior: BehaviorCommand,
) -> float:
    base = loop_config.interval_seconds
    if event.source != "idle":
        return base
    if report.away or behavior.animation in {"sleepy", "nap"}:
        return min(60.0, max(base, base * 4.0, behavior.duration_ms / 1000.0))
    if report.ignoring or behavior.animation in {"play", "excited", "alert"}:
        return max(2.0, base * 0.5)
    return base


def _local_day_key(now: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(now))


def _local_hour(now: float) -> int:
    return int(time.strftime("%H", time.localtime(now)))


def _log_loop_error(output: TextIO, event: _LoopEvent, message: str) -> None:
    _log_json(
        output,
        {
            "type": "loop_error",
            "input_id": event.id,
            "source": event.source,
            "error": message,
        },
    )


def _log_loop_behavior(
    output: TextIO, event: _LoopEvent, behavior: BehaviorCommand
) -> None:
    _log_json(
        output,
        {
            "type": "behavior_result",
            "input_id": event.id,
            "source": event.source,
            "animation": behavior.animation,
            "mood": behavior.mood,
            "text": behavior.text or "",
            "alert": behavior.alert,
            "duration_ms": behavior.duration_ms,
        },
    )


def _log_json(output: TextIO, payload: dict[str, object]) -> None:
    print(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=True),
        file=output,
        flush=True,
    )


def _adapt_loop_behavior(
    behavior: BehaviorCommand, loop_config: PetLoopConfig
) -> BehaviorCommand:
    if behavior.animation == "walk" and behavior.duration_ms < WALK_MIN_DURATION_MS:
        return BehaviorCommand(
            mood=behavior.mood,
            animation=behavior.animation,
            text=behavior.text,
            alert=behavior.alert,
            duration_ms=WALK_MIN_DURATION_MS,
        )

    if behavior.animation not in IDLE_CAPABLE_ANIMATIONS:
        return behavior

    loop_gap_ms = max(1000, int(loop_config.interval_seconds * 1000) // 2)
    duration_ms = min(behavior.duration_ms, IDLE_LOOP_DURATION_MS, loop_gap_ms)
    if duration_ms == behavior.duration_ms:
        return behavior

    return BehaviorCommand(
        mood=behavior.mood,
        animation=behavior.animation,
        text=behavior.text,
        alert=behavior.alert,
        duration_ms=duration_ms,
    )
