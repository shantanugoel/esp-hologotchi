from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from typing import Callable, TextIO

from .affect import Affect
from .body import BodyModel, BodySituation, BodyState
from .inputs import HostInput, HostInputQueue, InputError, TOUCH_SOURCE
from .memory import KIND_EPISODIC, MemoryStore
from .ollama import OllamaConfig, OllamaError, generate_proposal
from .presence import PresenceReport, PresenceSignals, PresenceTracker, SignalMailbox
from .prompt import load_pet_name
from .protocol import ANIMATION_TO_MOOD, BehaviorCommand, BehaviorProposal
from .reflection import consolidate_memory
from .state import (
    MESSAGE_APOLOGY,
    MESSAGE_HARSH,
    MESSAGE_PRAISE,
    PetState,
    build_stateful_prompt,
    classify_message,
    describe_presence_transition,
    describe_self_directed_situation,
)
from .transport import BehaviorClient, DeviceEndpoint, TransportError

GenerateBehavior = Callable[[str, OllamaConfig], "BehaviorCommand | BehaviorProposal"]
SleepFn = Callable[[float], None]
Clock = Callable[[], float]

IDLE_CAPABLE_ANIMATIONS = frozenset({"idle", "blink", "look_around"})
IDLE_LOOP_DURATION_MS = 3000
WALK_MIN_DURATION_MS = 6500
TOUCH_ACK_DURATION_MS = 8000
PRESENCE_SIGNAL_SOURCE = "presence_signal"
# Hold the device socket open through long idle/nap waits (device times out at
# 30s); send a bare-newline keepalive comfortably under that.
KEEPALIVE_INTERVAL_SECONDS = 15.0
# During a long sleep/away stretch, nudge a brief pose shift so the OLED never
# holds a perfectly static frame for minutes (SSD1351 burn-in guardrail).
BURN_IN_INTERVAL_SECONDS = 150.0
BURN_IN_DURATION_MS = 1500
# Sources whose event drives a deterministic affect update.
EFFECT_SOURCES = frozenset(
    {"direct_message", "build_result", "test_result", "important_alert", TOUCH_SOURCE}
)
# Sources that count as the owner directly interacting with Mochi (resets the
# "ignored" timer). An alert being raised is not the same as the owner engaging,
# but a physical touch is.
ENGAGEMENT_SOURCES = frozenset({"direct_message", TOUCH_SOURCE})
# A doubletap only triggers a play spike when Mochi has the energy for it;
# otherwise the play invite is acknowledged with a light boop.
PLAY_INVITE_MIN_ENERGY = 30.0
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
    gesture: str | None = None

    @classmethod
    def from_input(cls, item: HostInput) -> "_LoopEvent":
        return cls(
            id=item.id, source=item.source, event=item.event, gesture=item.gesture
        )


def run_pet_loop(
    loop_config: PetLoopConfig,
    model_config: OllamaConfig,
    endpoint: DeviceEndpoint | None,
    *,
    dry_run: bool = False,
    state: PetState | None = None,
    generate: GenerateBehavior = generate_proposal,
    sleep: SleepFn = time.sleep,
    now: Clock = time.time,
    input_queue: HostInputQueue | None = None,
    presence_tracker: PresenceTracker | None = None,
    signal_mailbox: SignalMailbox | None = None,
    body: BodyModel | None = None,
    memory: MemoryStore | None = None,
    log_events: bool = False,
    output: TextIO | None = None,
    error_output: TextIO | None = None,
) -> PetState:
    out = output or sys.stdout
    err = error_output or sys.stderr
    pet_state = state or PetState()
    pet_name = load_pet_name()
    presence = presence_tracker or PresenceTracker()
    body_model = body or BodyModel()
    _restore_persistent_state(memory, pet_state, presence, body_model)

    pending: _LoopEvent | None = _LoopEvent(
        id="initial", source="initial", event=loop_config.initial_event
    )
    cycles = 0

    touch_sink = (
        _make_uplink_touch_sink(input_queue, err, log_events)
        if input_queue is not None
        else None
    )
    client = (
        None
        if dry_run
        else BehaviorClient(_require_endpoint(endpoint), on_touch=touch_sink)
    )
    keepalive = client.send_keepalive if client is not None else _noop
    try:
        while loop_config.max_cycles is None or cycles < loop_config.max_cycles:
            now_ts = now()
            signals = (
                signal_mailbox.get(now_ts)
                if signal_mailbox is not None
                else PresenceSignals()
            )
            touch_has_presence = (
                pending is not None
                and pending.source == TOUCH_SOURCE
                and _has_presence_evidence(signals, presence)
            )
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
                if pending.source == PRESENCE_SIGNAL_SOURCE:
                    situation = describe_presence_transition(report, pet_name=pet_name)
                    event = _LoopEvent(
                        id=pending.id, source=pending.source, event=situation
                    )
                else:
                    event = pending
                    situation = pending.event
                    if pending.source in EFFECT_SOURCES:
                        _apply_owner_effects(pet_state.affect, pending)
                    if pending.source == TOUCH_SOURCE:
                        body_model.last_touch_at = now_ts
            else:
                situation = describe_self_directed_situation(
                    pet_state, report, pet_name=pet_name
                )
                event = _LoopEvent(id="idle", source="idle", event=situation)

            body_situation = body_model.advance(
                now_ts,
                affect=pet_state.affect,
                report=report,
                event_source=event.source,
                local_hour=_local_hour(now_ts),
            )
            if client is not None and event.source == TOUCH_SOURCE:
                try:
                    client.send(
                        _touch_ack_behavior(
                            event,
                            has_presence=touch_has_presence,
                            body=body_situation,
                        )
                    )
                except TransportError as exc:
                    if log_events:
                        _log_loop_error(err, event, f"device unavailable: {exc}")
                    else:
                        print(f"device unavailable: {exc}", file=err, flush=True)

            if memory is not None:
                _capture_memory(memory, event, report, pet_state.affect)
                consolidate_memory(memory)

            notes = _surprise_notes(pet_state, event, report, memory, now_ts, pet_name)
            if notes:
                situation = f"{situation}\n" + "\n".join(notes)

            memories = _retrieve_memories(memory, situation)

            prompt = build_stateful_prompt(
                pet_state,
                situation,
                pet_name=pet_name,
                presence=report,
                memories=memories,
                body=body_situation,
                now=now_ts,
            )
            proposed_body_state: str | None = None
            keepalive()
            try:
                result = generate(prompt, model_config)
            except OllamaError as exc:
                if log_events:
                    _log_loop_error(err, event, f"model unavailable: {exc}")
                else:
                    print(f"model unavailable: {exc}", file=err, flush=True)
                result = _fallback_behavior(event)
            behavior, proposed_body_state = _unpack_proposal(result)
            behavior = _apply_novelty(behavior, pet_state, event)
            behavior, body_state = body_model.resolve(
                body_situation, behavior, proposed_body_state, now=now_ts
            )
            behavior = _adapt_loop_behavior(behavior, loop_config)
            behavior = _burn_in_guardrail(behavior, pet_state, body_state, report, now_ts)

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
                memory.save_body(body_model.to_row())

            print(behavior.to_json_line(), end="", file=out, flush=True)
            if log_events:
                _log_loop_behavior(err, event, behavior)

            cycles += 1
            if loop_config.max_cycles is not None and cycles >= loop_config.max_cycles:
                break

            wait_seconds = _adaptive_interval(loop_config, report, event, behavior)
            if signal_mailbox is not None:
                expiry = signal_mailbox.next_expiry_at(now_ts)
                if expiry is not None:
                    wait_seconds = min(wait_seconds, max(1.0, expiry - now_ts))
            pending = _next_event(wait_seconds, input_queue, sleep, keepalive)
    finally:
        if client is not None:
            client.close()

    return pet_state


def _noop() -> None:
    return None


def _make_uplink_touch_sink(
    input_queue: HostInputQueue, err: TextIO, log_events: bool
) -> Callable[[str, "int | None"], None]:
    """Build the callback the transport reader uses for device touch uplink.

    Firmware-detected touches arrive on the device -> host channel and are fed
    into the same queue as HTTP ``/touch`` posts, so the loop reacts identically
    whichever path delivered the gesture.
    """

    def sink(gesture: str, duration_ms: "int | None") -> None:
        try:
            item = input_queue.submit_touch(gesture, duration_ms)
        except InputError:
            return
        if log_events:
            print(
                json.dumps(
                    {
                        "type": "input",
                        "status": "accepted",
                        "id": item.id,
                        "source": item.source,
                        "transport": "tcp",
                        "event": item.event,
                    },
                    separators=(",", ":"),
                    ensure_ascii=True,
                ),
                file=err,
                flush=True,
            )

    return sink


def _unpack_proposal(
    result: "BehaviorCommand | BehaviorProposal",
) -> tuple[BehaviorCommand, str | None]:
    if isinstance(result, BehaviorProposal):
        return result.to_behavior_command(), result.body_state
    return result, None


def _restore_persistent_state(
    memory: MemoryStore | None,
    pet_state: PetState,
    presence: PresenceTracker,
    body_model: BodyModel,
) -> None:
    if memory is None:
        return
    stored = memory.load_affect()
    if stored.affect:
        pet_state.affect = Affect.from_row(stored.affect)
    if stored.last_interaction is not None:
        presence.last_interaction = stored.last_interaction
    if stored.body:
        restored = BodyModel.from_row(stored.body, config=body_model.config)
        body_model.state = restored.state
        body_model.state_since = restored.state_since
        body_model.sleep_started_at = restored.sleep_started_at
        body_model.last_touch_at = restored.last_touch_at


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
    elif event.source == TOUCH_SOURCE:
        _apply_touch_effects(affect, event.gesture)


def _apply_touch_effects(affect: Affect, gesture: str | None) -> None:
    if gesture == "hold":
        affect.register_pet()
    elif gesture == "doubletap" and affect.energy >= PLAY_INVITE_MIN_ENERGY:
        affect.register_play_invite()
    else:
        # A tap, or a play invite Mochi is too tired to accept: a light boop.
        affect.register_boop()


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
    if source == TOUCH_SOURCE:
        return _touch_memory_signal(event.gesture)
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


def _touch_memory_signal(
    gesture: str | None,
) -> tuple[int, int, bool, bool, list[str]]:
    # Touch is always owner-initiated; a sustained pet is the most salient
    # affection moment, a play invite next, a quick boop least. The doubletap is
    # tagged as an invite regardless of whether Mochi had the energy to accept.
    if gesture == "hold":
        return 60, 60, True, False, ["touch", "affection"]
    if gesture == "doubletap":
        return 50, 55, True, False, ["touch", "play_invite"]
    return 40, 45, True, False, ["touch", "boop"]


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
    pet_name: str,
) -> list[str]:
    notes: list[str] = []
    special = _record_special_moment(state, event, now, memory, pet_name)
    if special is not None:
        notes.append(f"Rare special moment: {special}")

    if event.source == "idle":
        nudge = _self_nudge_note(state, report, now, pet_name)
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
    pet_name: str,
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
                return (
                    f"late-night desk beat; {pet_name} may get sleepy or gently dramatic."
                )
        if hour >= 22:
            key = f"{day_key}:night"
            if state.last_daybeat_key != key:
                state.last_daybeat_key = key
                return f"night desk beat; {pet_name} may wind down toward sleep."
    return None


def _self_nudge_note(
    state: PetState, report: PresenceReport, now: float, pet_name: str
) -> str | None:
    if now - state.last_self_nudge_at < SELF_NUDGE_MIN_SECONDS:
        return None
    affect = state.affect
    if report.ignoring and report.ignored_seconds >= SELF_ALERT_IGNORED_SECONDS:
        state.last_self_nudge_at = now
        return (
            "Self-made attention alert: the owner has been heads-down for about "
            f"two hours. {pet_name} may ask for attention once, gently, without guilt."
        )
    if report.away:
        return None
    if affect.social < 25 or affect.loneliness >= 55:
        state.last_self_nudge_at = now
        return f"Self-initiated nudge: {pet_name} may ask for a tiny bit of attention."
    if affect.play < 25 or affect.stimulation < 25:
        state.last_self_nudge_at = now
        return f"Self-initiated nudge: {pet_name} may start a tiny game or patrol."
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
    if event.source == TOUCH_SOURCE:
        return BehaviorCommand(
            mood=ANIMATION_TO_MOOD["confused"],
            animation="confused",
            text="???",
            alert=False,
            duration_ms=4000,
        )

    return BehaviorCommand(
        mood="calm",
        animation="idle",
        text=None,
        alert=False,
        duration_ms=5000,
    )


def _has_presence_evidence(signals: PresenceSignals, presence: PresenceTracker) -> bool:
    if signals.present is True:
        return True
    if signals.present is False or signals.screen_locked is True:
        return False
    if signals.idle_seconds is not None:
        return signals.idle_seconds < presence.config.away_idle_seconds
    if signals.screen_locked is False or signals.foreground_app:
        return True
    return False


def _touch_ack_behavior(
    event: _LoopEvent, *, has_presence: bool, body: BodySituation
) -> BehaviorCommand:
    if not has_presence:
        animation = "confused"
        text = _touch_text(event, _NO_PRESENCE_TOUCH_TEXT)
    elif body.state is BodyState.SLEEPING:
        animation = "sleepy"
        text = _touch_text(event, _SLEEP_TOUCH_TEXT)
    else:
        animation = "excited"
        text = _touch_text(event, _LOVE_TOUCH_TEXT)
    return BehaviorCommand(
        mood=ANIMATION_TO_MOOD[animation],
        animation=animation,
        text=text,
        alert=False,
        duration_ms=TOUCH_ACK_DURATION_MS,
    )


_NO_PRESENCE_TOUCH_TEXT: dict[str, tuple[str, ...]] = {
    "tap": ("who you?", "new hand?", "huh? you?", "boop ghost?"),
    "hold": ("human? there?", "still you?", "hand there?", "hello hand?"),
    "doubletap": ("whoa? whoa?", "two boops?", "wait what?", "again??"),
}
_LOVE_TOUCH_TEXT: dict[str, tuple[str, ...]] = {
    "tap": ("love!", "luv you", "big love", "tail love"),
    "hold": ("love you", "soft love", "warm love", "best human"),
    "doubletap": ("love love", "boop love", "more love", "yes love"),
}
_SLEEP_TOUCH_TEXT: dict[str, tuple[str, ...]] = {
    "tap": ("let me sleep", "sleep pls", "five more", "zzz pls"),
    "hold": ("still eepy", "sleepy pls", "dreaming", "soft sleep"),
    "doubletap": ("no zoomies", "zzz zoomies", "later pls", "eepy eepy"),
}


def _touch_text(event: _LoopEvent, options: dict[str, tuple[str, ...]]) -> str:
    gesture = event.gesture if event.gesture in options else "tap"
    choices = options[gesture]
    return choices[_event_index(event.id) % len(choices)]


def _event_index(input_id: str) -> int:
    try:
        return int(input_id.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return 0


def _next_event(
    wait_seconds: float,
    input_queue: HostInputQueue | None,
    sleep: SleepFn,
    keepalive: Callable[[], None] = _noop,
) -> _LoopEvent | None:
    if input_queue is not None:
        item = input_queue.get_nowait()
        if item is not None:
            return _LoopEvent.from_input(item)

    # Only chunk the wait when there is a live socket to keep alive; with no
    # keepalive (dry run / no device) a single wait keeps timing simple.
    chunked = keepalive is not _noop
    remaining = wait_seconds
    while remaining > 0:
        chunk = min(KEEPALIVE_INTERVAL_SECONDS, remaining) if chunked else remaining
        if input_queue is None:
            sleep(chunk)
        else:
            item = input_queue.wait(chunk)
            if item is not None:
                return _LoopEvent.from_input(item)
        remaining -= chunk
        if chunked and remaining > 0:
            # Still waiting: keep the device socket alive so an immediate event or
            # the next behavior frame is not blocked by a dropped connection.
            keepalive()
    return None


# Poses that hold pixels nearly static long enough to risk OLED burn-in.
STATIC_ANIMATIONS = frozenset({"idle", "nap", "sleepy"})


def _burn_in_guardrail(
    behavior: BehaviorCommand,
    state: PetState,
    body_state: BodyState,
    report: PresenceReport,
    now: float,
) -> BehaviorCommand:
    del report
    static = body_state in {BodyState.SLEEPING, BodyState.DROWSY} or (
        behavior.animation in STATIC_ANIMATIONS
    )
    last_animation = state.recent_animations[-1] if state.recent_animations else None
    if not static or behavior.animation != last_animation:
        # Either Mochi is visibly moving or the pose just changed: pixels shifted,
        # so restart the burn-in timer and leave the frame untouched.
        state.last_micromotion_at = now
        return behavior
    if state.last_micromotion_at <= 0:
        state.last_micromotion_at = now
        return behavior
    if now - state.last_micromotion_at < BURN_IN_INTERVAL_SECONDS:
        return behavior

    # The same static pose has been held too long. A brief sleep-coherent flicker
    # shifts pixels; the next tick returns to the resting pose.
    state.last_micromotion_at = now
    return BehaviorCommand(
        mood=ANIMATION_TO_MOOD["blink"],
        animation="blink",
        text=None,
        alert=False,
        duration_ms=BURN_IN_DURATION_MS,
    )


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
