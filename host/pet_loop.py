from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from typing import Callable, TextIO

from .inputs import HostInput, HostInputQueue
from .ollama import OllamaConfig, OllamaError, generate_behavior
from .protocol import BehaviorCommand
from .state import PetState, build_stateful_prompt
from .transport import BehaviorClient, DeviceEndpoint, TransportError

GenerateBehavior = Callable[[str, OllamaConfig], BehaviorCommand]
SleepFn = Callable[[float], None]
IDLE_CAPABLE_ANIMATIONS = frozenset({"idle", "blink", "look_around"})
IDLE_LOOP_DURATION_MS = 3000


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
    input_queue: HostInputQueue | None = None,
    log_events: bool = False,
    output: TextIO | None = None,
    error_output: TextIO | None = None,
) -> PetState:
    out = output or sys.stdout
    err = error_output or sys.stderr
    pet_state = state or PetState()
    event = _LoopEvent(id="initial", source="initial", event=loop_config.initial_event)
    cycles = 0

    client = None if dry_run else BehaviorClient(_require_endpoint(endpoint))
    try:
        while loop_config.max_cycles is None or cycles < loop_config.max_cycles:
            prompt = build_stateful_prompt(pet_state, event.event)
            try:
                behavior = generate(prompt, model_config)
            except OllamaError as exc:
                if log_events:
                    _log_loop_error(err, event, f"model unavailable: {exc}")
                else:
                    print(f"model unavailable: {exc}", file=err, flush=True)
                behavior = _fallback_behavior()
            behavior = _adapt_loop_behavior(behavior, loop_config)

            if client is not None:
                try:
                    client.send(behavior)
                except TransportError as exc:
                    if log_events:
                        _log_loop_error(err, event, f"device unavailable: {exc}")
                    else:
                        print(f"device unavailable: {exc}", file=err, flush=True)

            pet_state.observe(behavior, event.event)
            print(behavior.to_json_line(), end="", file=out, flush=True)
            if log_events:
                _log_loop_behavior(err, event, behavior)

            cycles += 1
            if loop_config.max_cycles is not None and cycles >= loop_config.max_cycles:
                break

            event = _next_event(loop_config, pet_state, input_queue, sleep)
    finally:
        if client is not None:
            client.close()

    return pet_state


def _require_endpoint(endpoint: DeviceEndpoint | None) -> DeviceEndpoint:
    if endpoint is None:
        raise ValueError("endpoint is required unless dry_run is enabled")
    return endpoint


def _fallback_behavior() -> BehaviorCommand:
    return BehaviorCommand(
        mood="calm",
        animation="idle",
        text=None,
        alert=False,
        duration_ms=5000,
    )


def _next_event(
    loop_config: PetLoopConfig,
    pet_state: PetState,
    input_queue: HostInputQueue | None,
    sleep: SleepFn,
) -> _LoopEvent:
    if input_queue is None:
        sleep(loop_config.interval_seconds)
        return _idle_event(pet_state)

    item = input_queue.get_nowait()
    if item is not None:
        return _LoopEvent.from_input(item)
    item = input_queue.wait(loop_config.interval_seconds)
    if item is not None:
        return _LoopEvent.from_input(item)
    return _idle_event(pet_state)


def _idle_event(pet_state: PetState) -> _LoopEvent:
    return _LoopEvent(id="idle", source="idle", event=pet_state.idle_event())


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
