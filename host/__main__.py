from __future__ import annotations

import argparse
import sys

from .control import ControlServerConfig, start_control_server
from .inputs import HostInputQueue
from .ollama import OllamaConfig, generate_behavior
from .pet_loop import PetLoopConfig, run_pet_loop
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
        help="Expose a small HTTP input endpoint while --loop is running.",
    )
    parser.add_argument(
        "--message-bind-host",
        default="127.0.0.1",
        help=(
            "Host/IP for the HTTP message endpoint. Defaults to localhost; "
            "use 0.0.0.0 to accept LAN clients."
        ),
    )
    parser.add_argument(
        "--message-port",
        type=int,
        default=8787,
        help="Port for the HTTP message endpoint.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

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
        control_server = (
            start_control_server(
                ControlServerConfig(
                    bind_host=args.message_bind_host,
                    port=args.message_port,
                ),
                input_queue,
            )
            if input_queue is not None
            else None
        )
        try:
            if control_server is not None:
                bind_host, bind_port = control_server.address
                print(
                    f"message endpoint listening on http://{bind_host}:{bind_port}",
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
                log_events=args.serve,
            )
        finally:
            if control_server is not None:
                control_server.close()
        return 0

    behavior = generate_behavior(args.prompt, model_config)
    if not args.dry_run:
        assert endpoint is not None
        send_behavior(behavior, endpoint)
    print(behavior.to_json_line(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
