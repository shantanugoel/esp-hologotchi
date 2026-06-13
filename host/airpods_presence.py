"""Optional AirPods presence helper (Phase V2a, after core presence).

This is the first explicit-presence *source* for ``/presence``. It polls the
local Bluetooth stack for a named device (e.g. the owner's AirPods) and POSTs a
minimal presence frame to the host control server:

    {"present": true,  "source": "airpods", "ttl_seconds": 30}
    {"present": false, "source": "airpods", "ttl_seconds": 30}

It is deliberately dumb: it knows nothing about Mochi's feelings or body state,
it only reports whether the named device is connected. Bluetooth probing is
brittle and platform-specific, so the brittle parts (subprocess + parsing) are
isolated and the testable parts (debounce, "post only on change", payload) are
pure.

Run it as a separate process so it never gates the core behavior:

    python -m host.airpods_presence --name "Shantanu's AirPods" \
        --url http://localhost:8787/presence

Debounce: a candidate reading must be seen on two consecutive polls before the
confirmed state flips, so a single noisy reading never flaps presence. The
debounced state is re-posted on every poll (a lightweight heartbeat) so the
host's per-source TTL stays fresh while the device remains connected; only a
debounced *change* is logged. Logging is limited to connection-state changes and
POST errors.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from typing import Callable, Sequence
from urllib import error, request

DEFAULT_URL = "http://localhost:8787/presence"
DEFAULT_SOURCE = "airpods"
DEFAULT_TTL_SECONDS = 30.0
DEFAULT_INTERVAL_SECONDS = 7.0
DEFAULT_COMMAND_TIMEOUT = 5.0
# A candidate reading must repeat this many times in a row before it is accepted.
REQUIRED_CONSECUTIVE = 2

BACKENDS = ("auto", "blueutil", "system_profiler", "bluetoothctl")

Reading = bool | None
Probe = Callable[[], Reading]
Poster = Callable[[bool], None]
Logger = Callable[[str], None]
SleepFn = Callable[[float], None]
CommandRunner = Callable[[Sequence[str]], str | None]


class PresenceDebouncer:
    """Confirm a reading only after it repeats ``REQUIRED_CONSECUTIVE`` times.

    ``observe`` returns the newly confirmed state when it flips (and should be
    posted), or ``None`` when nothing changed. Unknown readings (``None``) are
    ignored so a failed probe never flips presence.
    """

    def __init__(self, *, required: int = REQUIRED_CONSECUTIVE) -> None:
        if required < 1:
            raise ValueError("required must be at least 1")
        self._required = required
        self._confirmed: bool | None = None
        self._candidate: bool | None = None
        self._count = 0

    @property
    def confirmed(self) -> bool | None:
        return self._confirmed

    def observe(self, reading: Reading) -> bool | None:
        if reading is None:
            # A failed/unknown probe breaks the consecutive run: it is neither a
            # flip nor evidence for the candidate. The confirmed state is kept.
            self._candidate = None
            self._count = 0
            return None
        if reading == self._confirmed:
            self._candidate = None
            self._count = 0
            return None
        if reading == self._candidate:
            self._count += 1
        else:
            self._candidate = reading
            self._count = 1
        if self._count >= self._required:
            self._confirmed = reading
            self._candidate = None
            self._count = 0
            return reading
        return None


def run(
    probe: Probe,
    poster: Poster,
    *,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    sleep: SleepFn = time.sleep,
    log: Logger | None = None,
    max_polls: int | None = None,
    debouncer: PresenceDebouncer | None = None,
) -> PresenceDebouncer:
    """Poll ``probe``, log debounced changes, and heartbeat the current state.

    Once a state is confirmed it is re-posted on every poll so the host's
    per-source TTL stays fresh while the device remains connected. Idempotent
    re-posts do not wake the loop (the host only reacts to coarse presence
    transitions), but they keep the AirPods source authoritative. Only a
    debounced *change* is logged.
    """

    debounce = debouncer or PresenceDebouncer()
    emit = log or _default_logger
    polls = 0
    while max_polls is None or polls < max_polls:
        flipped = debounce.observe(probe())
        if flipped is not None:
            emit(f"airpods {'connected' if flipped else 'disconnected'}")
        state = debounce.confirmed
        if state is not None:
            poster(state)
        polls += 1
        if max_polls is not None and polls >= max_polls:
            break
        sleep(interval_seconds)
    return debounce


# -- posting ----------------------------------------------------------------


def post_presence(
    url: str,
    present: bool,
    *,
    source: str = DEFAULT_SOURCE,
    ttl_seconds: float = DEFAULT_TTL_SECONDS,
    timeout: float = DEFAULT_COMMAND_TIMEOUT,
    opener: Callable[..., object] | None = None,
) -> None:
    payload = json.dumps(
        {"present": bool(present), "source": source, "ttl_seconds": ttl_seconds}
    ).encode("utf-8")
    req = request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    open_fn = opener or request.urlopen
    with open_fn(req, timeout=timeout) as response:  # type: ignore[operator]
        response.read()


# -- probing (brittle, platform-specific) -----------------------------------


def _run_command(cmd: Sequence[str]) -> str | None:
    try:
        result = subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            timeout=DEFAULT_COMMAND_TIMEOUT,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout


def detect_backend(
    *, platform: str | None = None, which: Callable[[str], str | None] = shutil.which
) -> str:
    system = platform if platform is not None else sys.platform
    if system == "darwin":
        return "blueutil" if which("blueutil") else "system_profiler"
    return "bluetoothctl"


def make_probe(
    name: str, backend: str, *, runner: CommandRunner = _run_command
) -> Probe:
    resolved = detect_backend() if backend == "auto" else backend
    if resolved == "blueutil":
        return lambda: probe_blueutil(name, runner=runner)
    if resolved == "system_profiler":
        return lambda: probe_system_profiler(name, runner=runner)
    if resolved == "bluetoothctl":
        return lambda: probe_bluetoothctl(name, runner=runner)
    raise ValueError(f"unknown backend: {resolved!r}")


def probe_blueutil(name: str, *, runner: CommandRunner = _run_command) -> Reading:
    output = runner(["blueutil", "--paired"])
    if output is None:
        return None
    return parse_blueutil(output, name)


def probe_system_profiler(name: str, *, runner: CommandRunner = _run_command) -> Reading:
    output = runner(["system_profiler", "SPBluetoothDataType", "-json"])
    if output is None:
        return None
    return parse_system_profiler(output, name)


def probe_bluetoothctl(name: str, *, runner: CommandRunner = _run_command) -> Reading:
    output = runner(["bluetoothctl", "devices", "Connected"])
    if output is None:
        return None
    return parse_bluetoothctl(output, name)


_BLUEUTIL_NAME = re.compile(r'name:\s*"([^"]*)"')


def parse_blueutil(output: str, name: str) -> Reading:
    target = _normalize(name)
    for line in output.splitlines():
        match = _BLUEUTIL_NAME.search(line)
        if match is None or _normalize(match.group(1)) != target:
            continue
        # blueutil prints comma-separated fields, e.g. "not connected" or
        # "connected (master, -60 dBm)". Match the status field exactly so a
        # value like "connected: 0" elsewhere cannot be misread.
        for field in (part.strip().lower() for part in line.split(",")):
            if field.startswith("not connected"):
                return False
            if field == "connected" or field.startswith("connected ("):
                return True
        return False
    return None


def parse_system_profiler(output: str, name: str) -> Reading:
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return None
    controllers = data.get("SPBluetoothDataType", [])
    if not isinstance(controllers, list):
        return None
    found = None
    for controller in controllers:
        if not isinstance(controller, dict):
            continue
        if _names_contain(controller.get("device_connected"), name):
            return True
        if _names_contain(controller.get("device_not_connected"), name):
            found = False
    return found


def parse_bluetoothctl(output: str, name: str) -> Reading:
    # `bluetoothctl devices Connected` lists only connected devices, one per line
    # as "Device <MAC> <name>"; a paired but disconnected device does not appear.
    target = _normalize(name)
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped.lower().startswith("device "):
            continue
        parts = stripped.split(maxsplit=2)
        if len(parts) >= 3 and _normalize(parts[2]) == target:
            return True
    return False


def _names_contain(entries: object, name: str) -> bool:
    if not isinstance(entries, list):
        return False
    target = _normalize(name)
    for entry in entries:
        if isinstance(entry, dict):
            if any(_normalize(str(key)) == target for key in entry):
                return True
        elif isinstance(entry, str) and _normalize(entry) == target:
            return True
    return False


def _normalize(value: str) -> str:
    return " ".join(value.split()).lower()


# -- CLI --------------------------------------------------------------------


def _default_logger(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m host.airpods_presence",
        description=(
            "Poll Bluetooth for a named device and POST its presence to Mochi's "
            "host control server. Optional, platform-specific, localhost-only by "
            "default."
        ),
    )
    parser.add_argument(
        "--name",
        required=True,
        help='Bluetooth device name to track, e.g. "Shantanu\'s AirPods".',
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help="Presence endpoint to POST to. Defaults to localhost only.",
    )
    parser.add_argument(
        "--source",
        default=DEFAULT_SOURCE,
        help="Presence source key sent with each post.",
    )
    parser.add_argument(
        "--ttl-seconds",
        type=float,
        default=DEFAULT_TTL_SECONDS,
        help="TTL the host uses to expire this source between posts.",
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=DEFAULT_INTERVAL_SECONDS,
        help="Seconds between Bluetooth polls (5-10s is reasonable).",
    )
    parser.add_argument(
        "--backend",
        choices=BACKENDS,
        default="auto",
        help="Bluetooth probing backend. 'auto' picks one per platform.",
    )
    parser.add_argument(
        "--max-polls",
        type=int,
        default=None,
        help="Stop after this many polls. Defaults to running forever.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.interval_seconds <= 0:
        build_parser().error("--interval-seconds must be positive")
    if args.ttl_seconds <= 0:
        build_parser().error("--ttl-seconds must be positive")

    probe = make_probe(args.name, args.backend)

    def poster(present: bool) -> None:
        try:
            post_presence(
                args.url,
                present,
                source=args.source,
                ttl_seconds=args.ttl_seconds,
            )
        except (error.URLError, OSError, TimeoutError) as exc:
            _default_logger(f"presence POST to {args.url} failed: {exc}")

    run(
        probe,
        poster,
        interval_seconds=args.interval_seconds,
        max_polls=args.max_polls,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
