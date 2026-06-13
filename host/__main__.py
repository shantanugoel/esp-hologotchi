from __future__ import annotations

import argparse
import json
import os
import sys

from .control import ControlServerConfig, start_control_server
from .inputs import HostInputQueue
from .memory import MemoryStore, MemorySummary
from .ollama import OllamaConfig, generate_behavior
from .pet_loop import PetLoopConfig, run_pet_loop
from .presence import PresenceConfig, PresenceTracker, SignalMailbox
from .transport import DeviceEndpoint, send_behavior


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m host",
        description=(
            "Run Mochi's host brain with Ollama and send behavior updates to the ESP32."
        ),
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        default="Quiet desk time. Nothing urgent is happening.",
        help="The current moment or message Mochi should react to.",
    )
    parser.add_argument(
        "--device-host",
        help="IPv4 address or hostname of the ESP32 on the local network.",
    )
    parser.add_argument(
        "--device-port",
        type=int,
        default=4242,
        help="TCP control port exposed by the device firmware.",
    )
    parser.add_argument(
        "--backend",
        choices=["ollama"],
        default="ollama",
        help="Model backend. Phase 4 wires Ollama first.",
    )
    parser.add_argument(
        "--ollama-url",
        default="http://127.0.0.1:11434",
        help="Base URL for the local Ollama API.",
    )
    parser.add_argument(
        "--model-family",
        default="qwen3.5",
        help="Logical model family knob for the host configuration.",
    )
    parser.add_argument(
        "--model-preset",
        default="qwen3.5:4b",
        help="Concrete Ollama model preset to call.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=30.0,
        help="HTTP timeout for the Ollama request.",
    )
    parser.add_argument(
        "--ollama-keep-alive",
        default="30m",
        help=(
            "How long Ollama should keep the model loaded after each request. "
            "Use -1 to keep it loaded indefinitely."
        ),
    )
    parser.add_argument(
        "--ollama-think",
        action="store_true",
        help="Allow thinking output for models that support it. Disabled by default.",
    )
    parser.add_argument(
        "--ollama-num-predict",
        type=int,
        default=96,
        help="Maximum output tokens for one behavior JSON response.",
    )
    parser.add_argument(
        "--connect-retries",
        type=int,
        default=5,
        help="How many times to retry connecting to the device.",
    )
    parser.add_argument(
        "--retry-delay-seconds",
        type=float,
        default=0.5,
        help="Delay between device connection retries.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the validated wire payload without opening the TCP connection.",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Keep a small pet state and ask the model for the next behavior repeatedly.",
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=6.0,
        help="Delay between model decisions when --loop is enabled.",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=None,
        help="Stop the loop after this many behavior updates. Defaults to running forever.",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Expose small HTTP input endpoints while --loop is running.",
    )
    parser.add_argument(
        "--message-bind-host",
        default="127.0.0.1",
        help=(
            "Host/IP for the HTTP input endpoints. Defaults to localhost; "
            "use 0.0.0.0 to accept LAN clients."
        ),
    )
    parser.add_argument(
        "--message-port",
        type=int,
        default=8787,
        help="Port for the HTTP input endpoints.",
    )
    parser.add_argument(
        "--memory-db",
        default=_default_memory_db(),
        help=(
            "Path to Mochi's local SQLite memory. Persists needs, relationship "
            "state, and remembered moments across restarts."
        ),
    )
    parser.add_argument(
        "--no-memory",
        action="store_true",
        help="Run the loop without persisting or recalling memory.",
    )
    parser.add_argument(
        "--reset-memory",
        action="store_true",
        help="Erase all memories and relationship state in --memory-db, then exit.",
    )
    parser.add_argument(
        "--inspect-memory",
        action="store_true",
        help="Print a summary of the local memory store, then exit.",
    )
    parser.add_argument(
        "--engaged-window-seconds",
        type=float,
        default=PresenceConfig().engaged_window_seconds,
        help="How long after a direct interaction Mochi still counts as engaged.",
    )
    parser.add_argument(
        "--away-idle-seconds",
        type=float,
        default=PresenceConfig().away_idle_seconds,
        help="OS idle time (seconds) at which the owner is treated as away.",
    )
    parser.add_argument(
        "--focus-jealousy-seconds",
        type=float,
        default=PresenceConfig().focus_jealousy_seconds,
        help="How long heads-down on one foreground app before Mochi gets jealous.",
    )
    return parser


def _default_memory_db() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state"
    )
    return os.path.join(base, "hologotchi", "memory.db")


def _open_memory(db_path: str, *, writes_enabled: bool = True) -> MemoryStore:
    directory = os.path.dirname(db_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    return MemoryStore(db_path, writes_enabled=writes_enabled)


def _summary_dict(summary: MemorySummary) -> dict[str, object]:
    return {
        "total": summary.total,
        "writes_enabled": summary.writes_enabled,
        "by_kind": summary.by_kind,
        "by_source": summary.by_source,
        "top": [
            {
                "id": record.id,
                "source": record.source,
                "kind": record.kind,
                "summary": record.summary,
                "importance": round(record.importance, 2),
                "recall_count": record.recall_count,
            }
            for record in summary.top
        ],
    }


def _run_memory_admin(args: argparse.Namespace) -> int:
    store = _open_memory(args.memory_db)
    try:
        if args.reset_memory:
            store.reset()
            print(f"memory reset: {args.memory_db}", file=sys.stderr, flush=True)
        if args.inspect_memory:
            print(json.dumps(_summary_dict(store.summary()), indent=2))
    finally:
        store.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.reset_memory or args.inspect_memory:
        return _run_memory_admin(args)

    model_config = OllamaConfig(
        base_url=args.ollama_url,
        model_family=args.model_family,
        model_preset=args.model_preset,
        timeout_seconds=args.timeout_seconds,
        keep_alive=args.ollama_keep_alive,
        think=args.ollama_think,
        num_predict=args.ollama_num_predict,
    )
    if not args.dry_run and not args.device_host:
        parser.error("--device-host is required unless --dry-run is set")
    if args.serve and not args.loop:
        parser.error("--serve requires --loop")

    endpoint = (
        DeviceEndpoint(
            host=args.device_host,
            port=args.device_port,
            connect_retries=args.connect_retries,
            retry_delay_seconds=args.retry_delay_seconds,
            timeout_seconds=min(args.timeout_seconds, 10.0),
        )
        if args.device_host
        else None
    )

    if args.loop:
        input_queue = HostInputQueue() if args.serve else None
        memory = None if args.no_memory else _open_memory(args.memory_db)
        signal_mailbox = SignalMailbox(away_idle_seconds=args.away_idle_seconds)
        presence_tracker = PresenceTracker(
            PresenceConfig(
                engaged_window_seconds=args.engaged_window_seconds,
                away_idle_seconds=args.away_idle_seconds,
                focus_jealousy_seconds=args.focus_jealousy_seconds,
            )
        )
        control_server = (
            start_control_server(
                ControlServerConfig(
                    bind_host=args.message_bind_host,
                    port=args.message_port,
                ),
                input_queue,
                memory=memory,
                signal_mailbox=signal_mailbox,
            )
            if input_queue is not None
            else None
        )
        try:
            if control_server is not None:
                bind_host, bind_port = control_server.address
                print(
                    f"input endpoints listening on http://{bind_host}:{bind_port}",
                    file=sys.stderr,
                    flush=True,
                )
            run_pet_loop(
                PetLoopConfig(
                    interval_seconds=args.interval_seconds,
                    max_cycles=args.max_cycles,
                    initial_event=args.prompt,
                ),
                model_config,
                endpoint,
                dry_run=args.dry_run,
                input_queue=input_queue,
                presence_tracker=presence_tracker,
                signal_mailbox=signal_mailbox,
                memory=memory,
                log_events=args.serve,
            )
        finally:
            if control_server is not None:
                control_server.close()
            if memory is not None:
                memory.close()
        return 0

    behavior = generate_behavior(args.prompt, model_config)
    if not args.dry_run:
        assert endpoint is not None
        send_behavior(behavior, endpoint)
    print(behavior.to_json_line(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
