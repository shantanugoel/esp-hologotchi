# Hologotchi Plan

## Vision

Hologotchi is a tiny holographic AI pet for a desk: an ESP32-C3 drives a 128x128 RGB OLED viewed through a dichroic cube, while a local computer agent decides what the pet is feeling and doing. A small holographic creature lives beside the computer and reacts to what is happening on screen or in the background or what it hears (via the host program) etc.

The device should feel alive even when the host agent is disconnected, but the local computer is the brain. The ESP firmware is responsible for reliable display, animation, and protocol handling. The host agent is responsible for model calls, computer-context awareness, scheduling, and personality.

## V1 Goals

- Show a cute, readable pixel pet on the 1.5 inch 128x128 RGB OLED through the cube.
- Connect over USB serial first, with Wi-Fi treated as a later transport.
- Run a local Python agent on Linux/NVIDIA or macOS Apple Silicon.
- Use Ollama as the default model runner for cross-platform setup.
- Default to a small fast model such as `qwen3:1.7b`; allow `qwen3:4b` as a quality preset.
- React to computer context: active app/window, build/test results, music/state, notifications, and direct chat-style prompts where practical.
- Include demo-mode reactions that are strong enough to film without relying on a model.

## Hardware Assumptions

- MCU: ESP32-C3.
- Display: Waveshare 1.5 inch RGB OLED, 128x128, SSD1351, SPI, RGB565/65K color.
- Optics: dichroic cube, so final orientation and reflection correction are part of rendering.
- V1 transport: USB serial through the ESP programming/debug connection.
- Optional later hardware: touch input, IMU, light sensor, microphone, speaker, battery, and enclosure improvements.

## Firmware Responsibilities

- Initialize the ESP32-C3, SPI display, USB serial/logging, and Embassy tasks.
- Own the real-time pet renderer and local animation state.
- Keep an idle loop alive without the host agent.
- Parse compact host commands, validate them, and apply safe defaults for bad input.
- Show host connection state through a subtle animation or status behavior.
- Avoid doing model inference, long-term planning, or complex context interpretation on the ESP.

The existing `../esp-hologram` project is the reference for firmware shape: Rust, `no_std`, Embassy, `esp-hal`, cooperative tasks, fixed-size buffers, and cube-aware rendering. Its SSD1306/I2C display path should not be copied directly because this project uses an SSD1351/SPI RGB OLED.

## Host Agent Responsibilities

- Run as a Python service during V1.
- Connect to the device over USB serial and handle reconnects.
- Gather computer context through small adapters.
- Ask the local model for an emotion/action decision.
- Validate model output before sending anything to the ESP.
- Rate-limit and schedule reactions so the pet feels intentional rather than noisy.
- Provide a dry-run mode that prints serial frames without hardware.

The host should be provider-based:

- Default provider: Ollama HTTP API.
- Default model: `qwen3:1.7b`, optimized for low-latency always-on reactions.
- Quality preset: `qwen3:4b`, for machines that can spare more memory and latency.
- Later providers: llama.cpp server, LM Studio, MLX, or a remote API for comparison.

## Serial Protocol

Use newline-delimited JSON over USB serial. Commands should be small, versioned, and easy to inspect from a terminal.

Example reaction command:

```json
{"v":1,"type":"reaction","mood":"happy","action":"bounce","text":"nice build","intensity":0.8,"ttl_ms":5000}
```

Recommended V1 fields:

- `v`: protocol version, initially `1`.
- `type`: command type, initially `reaction`, `ping`, or `config`.
- `mood`: coarse emotional state.
- `action`: short animation trigger.
- `text`: optional short text bubble or caption.
- `intensity`: `0.0` to `1.0`, clamped by firmware.
- `ttl_ms`: how long the reaction may dominate before returning to normal idle behavior.

Initial moods:

- `idle`
- `happy`
- `curious`
- `thinking`
- `focused`
- `sleepy`
- `surprised`
- `sad`
- `celebrate`
- `error`

Initial actions:

- `blink`
- `bounce`
- `look_left`
- `look_right`
- `wave`
- `sparkle`
- `sweat`
- `shake`
- `sleep`
- `celebrate`

Invalid, oversized, or malformed commands must not panic the firmware. The host should validate first, and the firmware should still guard its parser.

## Visual Direction

V1 should optimize for a pixel-pet look:

- Strong silhouette visible through the cube.
- Few tiny details; prioritize phone-camera readability.
- Saturated accent colors on a mostly dark background.
- Big emotional changes: eyes, mouth, posture, bounce, particles, and color palette.
- Short text only, with strict length limits.
- Demo-friendly moments: build passed celebration, build failed worry, thinking loop, greeting, sleepy idle, surprised notification.

The renderer should start simple: full-frame RGB565 framebuffer, integer-friendly animation, and full-frame SPI flush. Partial updates, asset compression, and more advanced effects can wait until the display is stable.

## Roadmap

1. Bring up display hardware.
   - Add SSD1351 SPI driver/init.
   - Render color bars and orientation test.
   - Confirm cube reflection transform.

2. Build the pet renderer.
   - Add framebuffer drawing helpers.
   - Implement idle pet, moods, actions, particles, and short text.
   - Add demo mode for filming and hardware checks.

3. Add USB serial protocol.
   - Define command structs/enums.
   - Parse newline-delimited JSON or a compact equivalent suitable for `no_std`.
   - Add heartbeat and host-connected behavior.

4. Build the Python agent.
   - Serial transport with reconnects.
   - Ollama provider.
   - Strict JSON prompt and Pydantic validation.
   - Dry-run mode.

5. Add computer-context adapters.
   - Active app/window.
   - Terminal/build/test event hooks.
   - Music or media state where easy.
   - Notification/calendar integrations later.

6. Polish for viral demos.
   - Script repeatable demo scenarios.
   - Tune animation timing and color palettes for video.
   - Document wiring, flashing, and running the agent.

## Testing And Acceptance

Firmware checks:

- `cargo build` from `device/` for the ESP32-C3 target.
- Parser/state tests in host-compatible modules where possible.
- Manual serial tests with valid, malformed, oversized, and rapid-fire commands.
- Hardware smoke tests for SSD1351 init, color output, cube orientation, and animation rate.

Agent checks:

- Unit tests for model-output extraction, validation, event mapping, serial framing, and reconnect behavior.
- Mock Ollama responses for deterministic test runs.
- Dry-run mode that prints exactly what would be sent to the ESP.

V1 acceptance criteria:

- The pet boots into a lively idle animation with no host connected.
- The host agent connects over USB and changes mood/action within one second.
- Bad model output never crashes the agent or firmware.
- A 15 to 30 second video clearly communicates that a holographic pet is reacting to computer events.

## Open Questions For Later

- Whether Wi-Fi should become a first-class V2 transport or remain optional.
- Whether the pet should gain persistent life-sim state such as hunger, sleep, memory, and routines.
- Whether to add physical input sensors before or after the first polished software demo.
- Whether to package the host as a CLI service only or add a small desktop control UI.
