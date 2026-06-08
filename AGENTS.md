# AGENTS.md

Repo split:

- `device/`: ESP32-C3 Rust firmware.
- `host/`: local computer agent code when added.
- Keep device and host checks independent. Run firmware commands from `device/`.

Project-specific constraints:

- The local computer is the AI brain. Do not move model inference, long-term planning, or computer-context interpretation onto the ESP32-C3.
- The display target is the Waveshare 1.5 inch 128x128 RGB OLED: SSD1351 over SPI. Do not copy the old `../esp-hologram` SSD1306/I2C display path directly.
- The OLED is viewed through a dichroic cube, so final orientation and mirror/reflection correction are part of rendering correctness.
- Firmware should stay `no_std` friendly: fixed-size buffers, deterministic state machines, bounded parsing, and integer/fixed-point animation by default.
- The pet should have local idle/demo behavior so it still feels alive when the host is disconnected.

Protocol direction:

- V1 transport is USB serial.
- Preferred frame shape is newline-delimited JSON, versioned with `v: 1`.
- Initial command family is reaction-oriented: mood, action, optional short text, intensity, and TTL.
- The host validates model output before sending; firmware still clamps and rejects unsafe values.
- Keep logs parse-safe if they share the USB serial path with protocol frames.

Host-agent direction:

- Planned host stack is Python with Ollama as the default control/runtime API.
- Default model family is `qwen3.5`; use `qwen3.5:4b` as the default preset and `qwen3.5:2b` as the low-memory fallback.
- Treat backend, model family, and preset as separate configuration knobs.
- Prefer MLX-backed variants on macOS when they materially improve responsiveness or memory use on Apple Silicon.
- Keep host modules separated by transport, model provider, event adapters, validation, and scheduling.
- Computer-context reactions are in scope; full autonomous life-sim state is later.

Verification:

- Firmware: `cargo build`, `cargo fmt --all -- --check`, and `cargo clippy --all-features --workspace -- -D warnings` from `device/`.
- Host: add tests once `host/` exists for model-output validation, serial framing, reconnect behavior, and event-to-reaction mapping.
- Hardware changes need smoke tests for display init, color bars, cube orientation, serial command handling, and demo animations.
