from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import Callable, TextIO

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


def run_pet_loop(
    loop_config: PetLoopConfig,
    model_config: OllamaConfig,
    endpoint: DeviceEndpoint | None,
    *,
    dry_run: bool = False,
    state: PetState | None = None,
    generate: GenerateBehavior = generate_behavior,
    sleep: SleepFn = time.sleep,
    output: TextIO | None = None,
    error_output: TextIO | None = None,
) -> PetState:
    out = output or sys.stdout
    err = error_output or sys.stderr
    pet_state = state or PetState()
    event = loop_config.initial_event
    cycles = 0

    client = None if dry_run else BehaviorClient(_require_endpoint(endpoint))
    try:
        while loop_config.max_cycles is None or cycles < loop_config.max_cycles:
            prompt = build_stateful_prompt(pet_state, event)
            try:
                behavior = generate(prompt, model_config)
            except OllamaError as exc:
                print(f"model unavailable: {exc}", file=err, flush=True)
                behavior = _fallback_behavior()
            behavior = _adapt_loop_behavior(behavior, loop_config)

            if client is not None:
                try:
                    client.send(behavior)
                except TransportError as exc:
                    print(f"device unavailable: {exc}", file=err, flush=True)

            pet_state.observe(behavior, event)
            print(behavior.to_json_line(), end="", file=out, flush=True)

            cycles += 1
            if loop_config.max_cycles is not None and cycles >= loop_config.max_cycles:
                break

            sleep(loop_config.interval_seconds)
            event = pet_state.idle_event()
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
