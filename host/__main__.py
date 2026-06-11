from __future__ import annotations

import argparse

from .ollama import OllamaConfig, generate_behavior
from .transport import DeviceEndpoint, send_behavior


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m host",
        description=(
            "Generate one Mochi behavior with Ollama and send it to the ESP32 over TCP."
        ),
    )
    parser.add_argument(
        "prompt",
        help="The current moment or message Mochi should react to.",
    )
    parser.add_argument(
        "--device-host",
        required=True,
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    model_config = OllamaConfig(
        base_url=args.ollama_url,
        model_family=args.model_family,
        model_preset=args.model_preset,
        timeout_seconds=args.timeout_seconds,
    )
    endpoint = DeviceEndpoint(
        host=args.device_host,
        port=args.device_port,
        connect_retries=args.connect_retries,
        retry_delay_seconds=args.retry_delay_seconds,
        timeout_seconds=min(args.timeout_seconds, 10.0),
    )

    behavior = generate_behavior(args.prompt, model_config)
    if not args.dry_run:
        send_behavior(behavior, endpoint)

    print(behavior.to_json_line(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
