# Hologotchi Plan

## Vision

Hologotchi is a tiny holographic AI pet for a desk: an ESP32-C3 drives a 128x128 RGB OLED viewed through a dichroic cube, while a local computer agent decides what the pet is feeling and doing. A small holographic creature lives beside the computer and reacts to what is happening on screen or in the background through the host program.

The device should feel alive even when the host agent is disconnected, but the local computer is the brain. The ESP firmware is responsible for reliable display, animation, and protocol handling. The host agent is responsible for model calls, computer-context awareness, scheduling, and personality.

## V1 Goals

- Show a cute, readable pixel pet on the 1.5 inch 128x128 RGB OLED through the cube.
- Connect over USB serial first, with Wi-Fi treated as a later transport.
- Run a local Python agent on Linux/NVIDIA or macOS Apple Silicon.
- Use Ollama as the default model runner for cross-platform setup.
- Default to the `qwen3.5` family, with `qwen3.5:4b` as the main quality/default preset and `qwen3.5:2b` as the low-memory fallback.
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
- Default model family: `qwen3.5`.
- Default model preset: `qwen3.5:4b`, optimized for a balance of latency and intelligence.
- Low-memory fallback: `qwen3.5:2b`.
- Provider/model abstraction: the host should treat backend, family, and preset as separate config choices so the same control logic can run against Ollama, llama.cpp, LM Studio, or MLX-backed runtimes.
- macOS note: prefer MLX-backed variants when they materially improve memory use or responsiveness on Apple Silicon; keep the abstraction so Ollama remains the common path where that is simpler.

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
   - Model-family/preset abstraction.
   - Strict JSON prompt and Pydantic validation.
   - Dry-run mode.

5. Add computer-context adapters.
   - Active app/window.
   - Terminal/build/test event hooks.
   - Music or media state where easy.
   - Notification/calendar integrations later.

6. Polish for demos.
   - Script repeatable demo scenarios.
   - Tune animation timing and color palettes for video.
   - Document wiring, flashing, and running the agent.

## V2+ Architecture

The v1 host/device split should be the foundation for a longer-lived pet, not just a one-shot reaction renderer. Later versions should treat the model as a controller for state and behavior, not only as a source of mood labels.

### Persistent Pet State

- Keep the host as the canonical owner of long-term pet state.
- Model state should include fields such as mood, energy, hunger, attention, trust, boredom, recent interactions, and cooldowns.
- Device state should stay lightweight: current scene, current animation, short-lived effect timers, and the minimal mirror of host state needed for rendering.
- State changes should be event-driven and explicit so the model can intentionally cause decay, recovery, memory updates, and status shifts over time.

### Asynchronous Behaviors

- Separate “model produced an intent” from “pet is actually doing it now.”
- Add a behavior scheduler on the host that can queue, interrupt, defer, and resume behaviors.
- Behaviors should have triggers, durations, cooldowns, priorities, and optional preconditions.
- This is the layer that turns the pet from prebaked reactions into a creature that can continue acting after the original event has passed.

### Extensible Inputs

- Normalize all inputs into a shared versioned event schema before they reach the model.
- Add adapters for active app/window, terminal/build/test events, notifications, music/media, and later sensor inputs.
- New input sources should only require a new adapter and schema mapping, not a redesign of the model contract.
- Keep the model prompt centered on recent events plus the current pet state, rather than hardcoding source-specific logic everywhere.

### Graphics Abstraction

- Do not hardcode every arbitrary object as a firmware-native special case.
- Use a layered scene protocol so the host can express graphics as primitives, reusable named assets, or uploaded sprites.
- Recommended scene capabilities for later versions:
  - primitive shapes: line, rect, circle, polygon, text
  - reusable asset references: icons, symbols, recurring props
  - transforms: position, scale, rotation, flip
  - layers: foreground, background, effect layers
  - optional animation keyframes for short sequences
- A “house” should usually be composed from primitives or a reusable asset, not baked as a one-off firmware behavior.
- Only use raw frame streaming as an escape hatch for special cases; it should not be the default graphics path.

### Host/Model Strategy

- Keep backend, model family, and preset separate so the same control logic can run against Ollama, llama.cpp, LM Studio, or MLX-backed runtimes.
- Default family remains `qwen3.5`.
- Default preset remains `qwen3.5:4b`, with `qwen3.5:2b` as the low-memory fallback.
- Prefer MLX-backed variants on macOS when they materially improve responsiveness or memory use on Apple Silicon.
- Preserve the ability to swap to another family later if a better small model appears.

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
- Which persistent pet state fields should be present in the first long-term version.
- Which input adapters should ship first beyond computer context.
- Whether the host needs a desktop control UI or remains CLI/service-first.
- Whether later graphics should stop at scene primitives and sprites or add a richer animation timeline.
