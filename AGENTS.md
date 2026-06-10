# AGENTS.md

Repo split:

- `device/`: ESP32-C3 Rust firmware.
- `host/`: Python host service when added.
- Keep device and host checks independent. Run firmware commands from `device/`.

Project-specific constraints:

- The host or local network service is the AI brain. Do not move model inference, long-term planning, or computer-context interpretation onto the ESP32-C3.
- The display target is the Waveshare 1.5 inch 128x128 RGB OLED: SSD1351 over SPI. Do not copy the old `../esp-hologram` SSD1306/I2C display path directly.
- The OLED is viewed through a dichroic cube, so final orientation and mirror/reflection correction are part of rendering correctness.
- Firmware should stay `no_std` friendly: fixed-size buffers, deterministic state machines, bounded parsing, and integer/fixed-point animation by default.
- The pet should have local idle behavior so it still feels alive when the host is disconnected.
- Optimize for fast-to-market: one pet, one transport, one control loop, a few inputs.

Transport direction:

- V1 runtime transport is Wi-Fi only on the local network. USB may be used for flashing/power, but not for runtime control.
- Preferred wire shape is newline-delimited JSON over a single Wi-Fi connection, versioned with `v: 1`.
- Initial message family is high-level pet behavior: mood, animation, optional short text, alert flag, and duration.
- The host validates model output before sending; firmware still clamps lengths and rejects malformed frames.
- Do not build a generic event schema, scheduler, or scene graph in V1 unless real implementation proves it necessary.

Host-agent direction:

- Planned host stack is Python with Ollama as the default runtime API.
- Default model family is `qwen3.5`; use `qwen3.5:4b` as the default preset and `qwen3.5:2b` as the low-memory fallback.
- Keep backend, model family, and preset as separate configuration knobs.
- The host owns persistent pet state and the small input set for V1: direct prompts, build/test status, and one important alert path.
- Keep host modules separated by transport, model provider, pet state, inputs, and validation.

Hardware direction:

- ESP32-C3 is acceptable for V1. Only move to ESP32-S3 if Wi-Fi plus rendering proves too tight in RAM or the visuals grow materially more ambitious.

Verification:

- Firmware: `cargo build`, `cargo fmt --all -- --check`, and `cargo clippy --all-features --workspace -- -D warnings` from `device/`.
- Host: add tests once `host/` exists for model-output validation, Wi-Fi framing, reconnect behavior, and prompt-to-behavior mapping.
- Hardware changes need smoke tests for display init, color bars, cube orientation, Wi-Fi join/reconnect, receiving behavior updates, and idle/demo animations.
