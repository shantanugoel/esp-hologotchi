"""Scripted demo driver for recording Shiro.

This bypasses the LLM and the pet loop entirely and pushes a *fixed* sequence of
behavior frames straight to the device, so you can film a clean, repeatable take
and control the timing of every beat yourself.

Why a heartbeat: a behavior frame only holds its pose for its ``duration_ms``
(max 15 s) and then the firmware falls back to the local idle loop, and the
device's control socket times out after ~30 s of silence. So while a step is
"held", a background thread re-sends the current frame on an interval. That keeps
the pose on screen for as long as you want and keeps the connection alive.

Run it (device must be on Wi-Fi and reachable):

    uv run python -m host.tools.demo_driver --device-host 192.168.1.50

Manual mode (default) is best for recording: each step holds the pose until you
press Enter, so you advance the story exactly when your camera/voiceover is
ready.

    # auto-advance using each step's hold time, looped for a kiosk-style take
    uv run python -m host.tools.demo_driver --device-host 192.168.1.50 \
        --mode auto --loop

    # rehearse offline with no device, printing each wire frame
    uv run python -m host.tools.demo_driver --dry-run --list

You can also supply your own sequence as JSON (a list of step objects with the
same field names as :class:`Step`) via ``--sequence-file``.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from host.protocol import (
    ANIMATION_TO_MOOD,
    MAX_DURATION_MS,
    MIN_DURATION_MS,
    TEXT_MAX_LEN,
    BehaviorCommand,
    parse_behavior_response,
)
from host.transport import BehaviorClient, DeviceEndpoint, TransportError

DEFAULT_RESEND_INTERVAL_SECONDS = 5.0


@dataclass
class Step:
    """One beat of the demo: a pose Shiro holds plus film/voiceover cues."""

    animation: str
    text: str | None = None
    duration_ms: int = 8000
    hold_seconds: float = 4.0
    note: str = ""  # what to film / do at this beat
    vo: str = ""  # voiceover cue
    caption: str = ""  # on-screen caption overlay


# The hero storyboard: leave -> it misses you -> you come back -> boop.
HERO_SEQUENCE: tuple[Step, ...] = (
    Step(
        animation="look_around",
        text="watching",
        duration_ms=8000,
        hold_seconds=3.0,
        note="Open on a tight macro of the cube. Boop it; Shiro perks up.",
        vo="I built a tiny holographic dog.",
        caption="i made a hologram puppy",
    ),
    Step(
        animation="confused",
        text="where go?",
        duration_ms=8000,
        hold_seconds=3.0,
        note="Step out of frame (AirPods leave the room).",
        vo="and it actually knows when I leave.",
        caption="(it senses my airpods leave)",
    ),
    Step(
        animation="sleepy",
        text="so eepy",
        duration_ms=12000,
        hold_seconds=3.0,
        note="Tight on the cube, you are gone. Shiro droops.",
        vo="it just waits for me...",
        caption="a few mins later...",
    ),
    Step(
        animation="nap",
        text="zzz",
        duration_ms=15000,
        hold_seconds=4.0,
        note="Shiro curls up asleep.",
        vo="",
        caption="",
    ),
    Step(
        animation="excited",
        text="you're back!",
        duration_ms=6000,
        hold_seconds=3.0,
        note="Walk back into frame. Shiro pops awake.",
        vo="and when I come back...",
        caption="but when i come back",
    ),
    Step(
        animation="happy",
        text="missed you",
        duration_ms=8000,
        hold_seconds=3.0,
        note="Big tail-wag greeting.",
        vo="worth it.",
        caption="ok i'm never leaving",
    ),
    Step(
        animation="play",
        text="boop?",
        duration_ms=6000,
        hold_seconds=3.0,
        note="Boop / pet the cube. Shiro invites a game.",
        vo="good boy, Shiro.",
        caption="boop = instant serotonin",
    ),
    Step(
        animation="idle",
        text="cozy pup",
        duration_ms=8000,
        hold_seconds=2.0,
        note="Settle to calm; loops back to the opening shot.",
        vo="",
        caption="esp32 + local AI brain",
    ),
)


# Every animation once, for B-roll and picking shots.
TOUR_SEQUENCE: tuple[Step, ...] = (
    Step("idle", "cozy pup", note="Default calm resting pose."),
    Step("blink", "still here", duration_ms=4000, hold_seconds=2.0, note="Aliveness blink."),
    Step("look_around", "what dis?", note="Curious left/right scan."),
    Step("confused", "huh?", note="Head-tilt, unsure."),
    Step("walk", "patrol time", duration_ms=10000, hold_seconds=5.0, note="Little patrol."),
    Step("happy", "tail party", note="Tail-wag joy."),
    Step("play", "play?", note="Paw lift / play invite."),
    Step("excited", "bounce!", duration_ms=6000, note="Big joyful bounce."),
    Step("sleepy", "nap mode", duration_ms=12000, note="Drowsy loaf."),
    Step("nap", "zzz", duration_ms=15000, hold_seconds=5.0, note="Curled asleep."),
    Step("worried", "oh no", note="Ears down, concerned."),
    Step("alert", "look now", duration_ms=8000, note="Perked alert + border pulse."),
)

SEQUENCES: dict[str, tuple[Step, ...]] = {
    "hero": HERO_SEQUENCE,
    "tour": TOUR_SEQUENCE,
}


def build_command(step: Step) -> BehaviorCommand:
    """Turn a Step into a device-valid behavior frame (raises on bad input)."""

    animation = step.animation
    if animation not in ANIMATION_TO_MOOD:
        valid = ", ".join(sorted(ANIMATION_TO_MOOD))
        raise ValueError(f"unknown animation {animation!r}; valid animations: {valid}")

    mood = ANIMATION_TO_MOOD[animation]
    alert = animation == "alert"
    duration = max(MIN_DURATION_MS, min(MAX_DURATION_MS, int(step.duration_ms)))

    text = step.text.strip() if step.text else None
    if text is not None:
        if not text.isascii():
            raise ValueError(f"text must be ASCII so the device can render it: {text!r}")
        if len(text) > TEXT_MAX_LEN:
            raise ValueError(f"text must be at most {TEXT_MAX_LEN} chars: {text!r}")
        text = text or None

    command = BehaviorCommand(
        mood=mood, animation=animation, text=text, alert=alert, duration_ms=duration
    )
    # Safety net: prove the firmware parser would accept this exact frame.
    parse_behavior_response(command.to_json_line())
    return command


class PoseHolder:
    """Holds the current pose by re-sending its frame on a heartbeat.

    ``set_pose`` sends immediately so the pose changes the instant you advance;
    a daemon thread then re-sends the same frame every ``resend_interval`` so the
    device never reverts to idle and the socket never times out mid-take.
    """

    def __init__(self, client: BehaviorClient | None, resend_interval: float) -> None:
        self._client = client
        self._resend_interval = max(1.0, resend_interval)
        self._lock = threading.Lock()
        self._command: BehaviorCommand | None = None
        self._last_send = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="hologotchi-demo-heartbeat", daemon=True
        )

    def start(self) -> None:
        if self._client is not None:
            self._thread.start()

    def set_pose(self, command: BehaviorCommand) -> None:
        with self._lock:
            self._command = command
        self._send(command)

    def _send(self, command: BehaviorCommand) -> None:
        if self._client is None:
            return
        try:
            self._client.send(command)
        except TransportError as exc:
            print(f"  ! send failed: {exc}", file=sys.stderr)
        with self._lock:
            self._last_send = time.monotonic()

    def _run(self) -> None:
        while not self._stop.wait(0.25):
            with self._lock:
                command = self._command
                due = time.monotonic() - self._last_send >= self._resend_interval
            if command is not None and due:
                self._send(command)

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=1.5)


def _format_step(index: int, total: int, step: Step, command: BehaviorCommand) -> str:
    lines = [
        f"\n[{index + 1}/{total}] {step.animation}"
        + (f'  "{step.text}"' if step.text else ""),
        f"  frame  : {command.to_json_line().strip()}",
    ]
    if step.note:
        lines.append(f"  film   : {step.note}")
    if step.vo:
        lines.append(f"  say    : {step.vo}")
    if step.caption:
        lines.append(f"  caption: {step.caption}")
    return "\n".join(lines)


def run_manual(steps: list[Step], holder: PoseHolder, total: int) -> None:
    print(
        "\nManual mode. Controls: [Enter]=next  r=replay this beat  b=back  "
        "<number>=jump  q=quit\n"
    )
    index = 0
    while 0 <= index < total:
        step = steps[index]
        command = build_command(step)
        print(_format_step(index, total, step, command))
        holder.set_pose(command)

        choice = input("  > ").strip().lower()
        if choice in {"q", "quit"}:
            break
        if choice in {"r", "replay"}:
            continue  # re-enter the same beat (re-sends, restarts the animation)
        if choice in {"b", "back"}:
            index = max(0, index - 1)
            continue
        if choice.isdigit():
            target = int(choice) - 1
            if 0 <= target < total:
                index = target
            else:
                print(f"  (no beat {choice}; staying on {index + 1})")
            continue
        index += 1


def run_auto(
    steps: list[Step],
    holder: PoseHolder,
    total: int,
    *,
    hold_override: float | None,
    speed: float,
    loop: bool,
) -> None:
    speed = max(0.05, speed)
    print(
        f"\nAuto mode (speed x{speed:g}{', looping' if loop else ''}). Ctrl-C to stop.\n"
    )
    try:
        while True:
            for index, step in enumerate(steps):
                command = build_command(step)
                print(_format_step(index, total, step, command))
                holder.set_pose(command)
                hold = hold_override if hold_override is not None else step.hold_seconds
                time.sleep(max(0.0, hold) / speed)
            if not loop:
                break
    except KeyboardInterrupt:
        print("\nStopped.")


def load_sequence(args: argparse.Namespace) -> list[Step]:
    if args.sequence_file:
        raw = json.loads(Path(args.sequence_file).read_text(encoding="utf-8"))
        if not isinstance(raw, list) or not raw:
            raise ValueError("sequence file must be a non-empty JSON list of steps")
        allowed = set(Step.__dataclass_fields__)
        steps: list[Step] = []
        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                raise ValueError(f"step {i} must be a JSON object")
            unknown = set(item) - allowed
            if unknown:
                raise ValueError(f"step {i} has unknown fields: {sorted(unknown)}")
            steps.append(Step(**item))
        return steps
    return list(SEQUENCES[args.sequence])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m host.tools.demo_driver",
        description="Drive Shiro through a scripted demo sequence for recording.",
    )
    parser.add_argument("--device-host", help="IPv4 / hostname of the ESP32 on Wi-Fi.")
    parser.add_argument("--device-port", type=int, default=4242, help="Device control port.")
    parser.add_argument(
        "--sequence",
        choices=sorted(SEQUENCES),
        default="hero",
        help="Built-in sequence to run (ignored if --sequence-file is given).",
    )
    parser.add_argument(
        "--sequence-file",
        help="Path to a JSON list of step objects to run instead of a built-in.",
    )
    parser.add_argument(
        "--mode",
        choices=["manual", "auto"],
        default="manual",
        help="manual: advance on Enter (best for recording). auto: use hold times.",
    )
    parser.add_argument(
        "--hold",
        type=float,
        default=None,
        help="Auto mode: override every step's hold time (seconds).",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="Auto mode: time multiplier (2 = twice as fast).",
    )
    parser.add_argument("--loop", action="store_true", help="Auto mode: loop forever.")
    parser.add_argument("--start-step", type=int, default=1, help="1-based beat to start on.")
    parser.add_argument(
        "--resend-interval",
        type=float,
        default=DEFAULT_RESEND_INTERVAL_SECONDS,
        help="Seconds between heartbeat re-sends that hold the current pose.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the sequence (and validate every frame), then exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not connect to a device; just print frames as you advance.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        steps = load_sequence(args)
        commands = [build_command(step) for step in steps]  # validate up front
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    total = len(steps)

    if args.list:
        for index, (step, command) in enumerate(zip(steps, commands)):
            print(_format_step(index, total, step, command))
        print(f"\n{total} beats. mood is derived from animation; frames validated OK.")
        return 0

    start = min(max(args.start_step, 1), total) - 1
    steps = steps[start:]
    total = len(steps)

    if args.dry_run:
        holder = PoseHolder(None, args.resend_interval)
        holder.start()
        _drive(args, steps, holder, total)
        return 0

    if not args.device_host:
        print("error: --device-host is required (or use --dry-run / --list)", file=sys.stderr)
        return 2

    endpoint = DeviceEndpoint(host=args.device_host, port=args.device_port)
    print(f"Connecting to {endpoint.host}:{endpoint.port} ...")
    with BehaviorClient(endpoint) as client:
        holder = PoseHolder(client, args.resend_interval)
        holder.start()
        try:
            _drive(args, steps, holder, total)
        finally:
            # Leave Shiro calm rather than frozen on the last demo pose.
            try:
                holder.set_pose(build_command(Step(animation="idle", text="cozy pup")))
                time.sleep(0.2)
            except TransportError:
                pass
            holder.stop()
    print("Done.")
    return 0


def _drive(args: argparse.Namespace, steps: list[Step], holder: PoseHolder, total: int) -> None:
    if args.mode == "manual":
        run_manual(steps, holder, total)
    else:
        run_auto(
            steps,
            holder,
            total,
            hold_override=args.hold,
            speed=args.speed,
            loop=args.loop,
        )


if __name__ == "__main__":
    raise SystemExit(main())
