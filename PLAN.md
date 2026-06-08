# Hologotchi Plan

## Vision

Hologotchi is a tiny holographic AI pet for a desk: an ESP32-C3 drives a 128x128 RGB OLED viewed through a dichroic cube, while a local computer agent decides what the pet is feeling and doing. A small holographic creature lives beside the computer and reacts to what is happening on screen or in the background through the host program.

The device should feel alive even when the host agent is disconnected, but the local computer is the brain. The ESP firmware is responsible for reliable display, animation, and protocol handling. The host agent is responsible for model calls, computer-context awareness, scheduling, and personality.

The single success metric is emotional: a 15 to 30 second phone clip should make a stranger believe a little holographic creature is alive on the desk and reacting to the computer. Everything in this plan is in service of that clip and of a creature people want to keep around.

## V1 Goals

- Show a cute, readable, *recognizable* holographic creature on the 1.5 inch 128x128 RGB OLED through the cube. One character, not a generic mood renderer.
- Connect over USB serial first, with Wi-Fi treated as a later transport.
- Run a local Python agent on Linux/NVIDIA or macOS Apple Silicon.
- Use Ollama as the default model runner for cross-platform setup.
- Default to the `qwen3.5` family, with `qwen3.5:4b` as the main quality/default preset and `qwen3.5:2b` as the low-memory fallback.
- React to computer context: active app/window, build/test results, music/state, notifications, and direct chat-style prompts where practical.
- Use a two-tier reaction model: instant rule-based reflexes for snappy feedback, plus slower model-driven personality for flavor. The pet must feel responsive even before the model answers.
- Drive characterful audio cues from the host computer's own speakers, synced to reactions, with no added device hardware.
- Include demo-mode reactions that are strong enough to film without relying on a model.

## Character & Personality

The biggest lever is a specific, ownable creature people bond with, not a list of moods. This is the one creative decision to lock before art begins.

Design principles:

- **Silhouette first.** It must read instantly through the cube on a phone camera, at a glance, in motion.
- **Glow-friendly.** A dichroic-cube hologram rewards a luminous, self-lit creature on a dark background. Lean into emissive shapes, rim light, and particles instead of fine texture.
- **Minimal but expressive.** Personality should come from eyes, posture, bounce, glow intensity, and particles, not from text or tiny details.
- **Cheap to animate.** Favor a creature with no walk cycle (floats/hovers) so animation cost stays in integer/fixed-point budget.

Proposed default (confirm or swap): **a small floating holographic spirit/blob** — a luminous, hovering desk sprite with big expressive eyes and a soft particle aura. It floats (no legs to animate), has a strong round silhouette, glows naturally as a hologram, and can deform/squash for big emotions. Alternatives worth a quick sketch pass: a tiny ghost, a slime, a low-poly fox head, or an orb-with-eyes companion.

Personality baseline: curious, reactive, a little dramatic. It watches the screen, celebrates wins loudly, frets over failures, gets sleepy when idle, and peeks at notifications. The signature behaviors below should feel consistent with that personality.

> Decision needed: confirm the creature concept and give it a name. The rest of the renderer and asset work depends on this.

## Hardware Assumptions

- MCU: ESP32-C3.
- Display: Waveshare 1.5 inch RGB OLED, 128x128, SSD1351, SPI, RGB565/65K color.
- Optics: dichroic cube, so final orientation and reflection correction are part of rendering.
- V1 transport: USB serial through the ESP programming/debug connection (USB Serial/JTAG).
- Memory budget: a single 128x128 RGB565 framebuffer is 32 KB. The current heap is ~64 KB and the scaffold still links the unused Wi-Fi/embassy-net/smoltcp stack. For V1, drop those crates, raise the heap, and budget single-buffer vs double-buffer explicitly.
- SPI flush: a full-frame push is ~32 KB; plan SPI **DMA** (chunked to the descriptor limit) so the CPU can compute frame N+1 while frame N transfers. A blocking flush would stall rendering for several milliseconds per frame.
- Optional later hardware: touch input, IMU, light sensor, microphone, on-device speaker/piezo, battery, and enclosure improvements.

## Firmware Responsibilities

- Initialize the ESP32-C3, SPI display, USB serial/logging, and Embassy tasks.
- Own the real-time pet renderer and local animation state.
- Keep an idle loop alive without the host agent.
- Run a small deterministic **reflex layer**: clamp, validate, and immediately apply incoming reactions without waiting on anything host-side.
- Parse compact host commands, validate them, and apply safe defaults for bad input.
- Show host connection state through a subtle animation or status behavior.
- Keep firmware logging on a channel that does not corrupt protocol frames (see Serial Protocol).
- Avoid doing model inference, long-term planning, or complex context interpretation on the ESP.

The existing `../esp-hologram` project is the reference for firmware shape: Rust, `no_std`, Embassy, `esp-hal`, cooperative tasks, fixed-size buffers, and cube-aware rendering. Its SSD1306/I2C display path should not be copied directly because this project uses an SSD1351/SPI RGB OLED.

Driver note: the existing `ssd1351` crate targets `embedded-hal 0.2`, while `esp-hal ~1.1` exposes `embedded-hal 1.0`. Prefer a thin custom init plus DMA flush (reusing the datasheet/crate init sequence) over adopting the crate wholesale. Keep the drawing code `embedded-graphics`-compatible so the same logic can run in `embedded-graphics-simulator` on a laptop (see Rendering and Simulation).

## Host Agent Responsibilities

- Run as a Python service during V1.
- Connect to the device over USB serial and handle reconnects.
- Gather computer context through small adapters and normalize everything into the shared event schema.
- Run the reflex/personality split: map salient events to instant reflex reactions, and ask the local model for richer personality reactions in parallel.
- Ask the local model for an emotion/action decision using constrained generation (JSON schema / grammar) plus strict validation.
- Validate model output before sending anything to the ESP.
- Rate-limit and schedule reactions so the pet feels intentional rather than noisy, including a basic priority/replacement policy.
- Drive synced audio cues through the computer's speakers.
- Provide a dry-run mode that prints serial frames without hardware.

The host should be provider-based:

- Default provider: Ollama HTTP API.
- Default model family: `qwen3.5`.
- Default model preset: `qwen3.5:4b`, optimized for a balance of latency and intelligence.
- Low-memory fallback: `qwen3.5:2b`.
- Constrained generation: use the provider's JSON/schema/grammar mode so a small local model returns valid reaction objects, then validate with Pydantic. Never trust free-form model JSON.
- Provider/model abstraction: the host should treat backend, family, and preset as separate config choices so the same control logic can run against Ollama, llama.cpp, LM Studio, or MLX-backed runtimes.
- macOS note: prefer MLX-backed variants when they materially improve memory use or responsiveness on Apple Silicon; keep the abstraction so Ollama remains the common path where that is simpler.
- Vision opportunity: `qwen3.5` accepts image input, so feeding it occasional screenshots could let the pet literally "see" the screen. This is a strong angle but raises latency and memory, so it belongs behind the reflex layer and should stay an opt-in, lower-frequency path rather than the default reaction loop.

## Reaction Model: Reflexes vs Personality

Separate how fast the pet *feels* from how smart it *is*.

- **Reflexes (instant, rule-based, no model).** Deterministic mappings from salient events to reactions, fired immediately on the host (or even pre-registered on the device). Example: tests go red, the pet ducks and turns worried within a frame or two. Reflexes hide model latency and guarantee the pet always responds.
- **Personality (model-driven, slower, optional).** The model adds variation, short text, and less predictable flavor. It can extend or override a reflex once it arrives, but the pet never waits on it to acknowledge an event.

Latency budgets (keep these distinct in design and acceptance):

- Transport latency (serial command to on-screen animation): target well under one second; trivially achievable.
- Reflex latency (event to first reaction): near-instant; this is what makes the demo feel alive.
- Cognitive latency (event to model-driven reaction): variable, often more than one second, especially cold or without MLX. Must never block reflexes.

## Serial Protocol

Use newline-delimited JSON over USB serial. Commands should be small, versioned, forward-compatible, and easy to inspect from a terminal. The envelope is designed so the later behavior scheduler and richer scene messages do not require a protocol break.

Example reaction command:

```json
{"v":1,"type":"reaction","id":1042,"priority":5,"mood":"happy","action":"bounce","text":"nice build","intensity":80,"ttl_ms":5000}
```

Recommended V1 fields:

- `v`: protocol version, initially `1`.
- `type`: command type, initially `reaction`, `ping`, or `config`. Reserved for future scene/behavior message types.
- `id`: optional monotonic id for acknowledgement and de-duplication.
- `priority`: integer; higher priority replaces an active lower-priority reaction. Seeds the V2 scheduler without a protocol change.
- `mood`: coarse emotional state; selects the base pose and palette.
- `action`: short animation trigger; plays as an overlay on top of the current mood.
- `text`: optional short text bubble or caption.
- `intensity`: integer `0` to `100`, clamped by firmware. Integer (not float) to keep `no_std` parsing bounded and FPU-free.
- `ttl_ms`: how long the reaction may dominate before returning to normal idle/personality behavior.

Mood/action relationship: `mood` is the base layer (pose + palette), `action` is a short overlay. Not every mood/action pair needs bespoke art; define the valid/blocked combinations rather than authoring all 10x10.

Overlap/replacement policy: a reaction with equal-or-higher `priority` replaces the active one; lower-priority reactions are dropped while one is active; on `ttl_ms` expiry the pet returns to idle or the personality baseline.

Log/channel separation (important): on the ESP32-C3, `esp-println` logs and protocol frames default to the *same* USB Serial/JTAG endpoint, which would corrupt parsing. V1 must separate them: route logs to a dedicated UART (preferred), or keep protocol frames as single-line JSON (starting with `{`, ending with `}`) and have the host treat every other line as a log to discard.

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

## Computer-Context Event Schema

Normalize every input into one shared, versioned event schema on the host *before* it reaches reflexes or the model. This is the cheapest forward-compatibility investment in V1: adding a new input source later becomes "write an adapter," not "redesign the reaction logic."

Recommended event shape:

- `v`: schema version, initially `1`.
- `source`: adapter origin, e.g. `build`, `window`, `media`, `notification`, `chat`.
- `kind`: specific event, e.g. `test_failed`, `app_focused`, `track_changed`.
- `ts`: timestamp.
- `salience`/`priority`: how strongly this should compete for the pet's attention.
- `payload`: source-specific details kept out of the core contract.

Reflexes and the model prompt both consume this normalized stream. The model prompt stays centered on recent events plus current pet state, rather than hardcoding source-specific logic. This is the same schema the V2 architecture relies on, pulled forward intentionally.

## Audio

Sound is half of feeling alive, and the host is already a computer with speakers, so V1 gets audio for free.

- Drive short, characterful chirps/stings from the host, synced to the reaction the pet is playing (Tamagotchi-style, not realistic).
- Carry the audio cue in the same reaction decision so sound and animation stay in lockstep.
- Keep cues mapped to moods/actions so the personality reads with eyes closed.
- Optional later: an on-device piezo/buzzer or speaker for standalone, host-disconnected charm.

## Visual Direction

V1 should optimize for a holographic pixel-pet look:

- Strong silhouette visible through the cube.
- Few tiny details; prioritize phone-camera readability.
- Saturated, emissive accent colors on a mostly dark background.
- Big emotional changes: eyes, mouth, posture, bounce, glow, particles, and color palette.
- Short text only, with strict length limits; lean on motion over captions because small text through the cube is hard to read.
- Demo-friendly moments: build passed celebration, build failed worry, thinking loop, greeting, sleepy idle, surprised notification.

The renderer should start simple: full-frame RGB565 framebuffer, integer-friendly animation, and DMA full-frame flush. Partial updates, asset compression, and more advanced effects can wait until the display is stable.

### Signature Moments / Hero Demo

Pick ONE hero clip and obsess over its timing; breadth comes later.

- Strawman hero moment: tests go red and the pet visibly panics/ducks, then tests go green and it erupts in celebration with particles and a chirp. This single before/after beat communicates "it reacts to my computer" in five seconds.
- Capture clips continuously through development, not only in the final polish phase. Treat demo-ability as a running constraint.

### Rendering and Simulation

Decide early: keep drawing code `embedded-graphics`-compatible so it can also compile for `std` and run in `embedded-graphics-simulator` on the laptop. This enables 60fps iteration on animations and palettes without reflashing, plus snapshot tests in CI. The trade-off is a slightly heavier abstraction than a hand-rolled RGB565 framebuffer; the iteration speed is worth it. Pair it with a thin custom DMA flush for the SSD1351.

## Roadmap

Sequencing principle: lock the few forward-compatible contracts first (protocol envelope, event schema, renderer seam), then prove the full loop end-to-end as early as possible, then add depth. This ordering trades a little up-front design for avoiding two likely rewrites later: the protocol/parser and the input-to-reaction logic.

0. Lock contracts and skeleton (prevents later rewrites).
   - Freeze the protocol envelope (versioned `type`, `id`, `priority`, integer `intensity`, `ttl_ms`).
   - Define the normalized computer-context event schema.
   - Decide rendering approach (`embedded-graphics`-compatible + simulator vs raw framebuffer).
   - Stand up the `host/` Python package skeleton and the device serial/log channel split.

1. Bring up display hardware.
   - Add SSD1351 SPI init and a DMA full-frame flush.
   - Render color bars and an *asymmetric* orientation test (e.g. an "F" or arrow) so mirror/rotation is unambiguous.
   - Confirm and codify the cube reflection transform (render-time transform vs pre-mirrored assets).

2. End-to-end tracer bullet (vertical slice).
   - Render one mood on the device.
   - Minimal host script sends one reaction on one real event (test pass/fail) over serial.
   - Validate the whole loop, including log/protocol channel separation, before building depth. This proves the core thesis early.

3. Pet renderer depth and reflex layer.
   - Add framebuffer drawing helpers and the chosen character's idle.
   - Implement moods (base pose/palette) and actions (overlays), particles, glow, and short text.
   - Add the device-side reflex/clamp behavior and a demo mode for filming and hardware checks.

4. Host agent depth.
   - Serial transport with reconnects.
   - Ollama provider plus model-family/preset abstraction.
   - Constrained JSON generation and Pydantic validation.
   - Two-tier reflex/personality split and a scheduler-lite priority/replacement policy.
   - Host-driven audio cues and dry-run mode.

5. Computer-context adapters, ordered by demo ROI.
   - Build/test events first (the hero demo); prefer a reliable wrapper command or shell `precmd`/`PROMPT_COMMAND` exit-code hook over scraping arbitrary terminals.
   - Active app/window next.
   - Music/media state where easy.
   - Notification/calendar integrations later. All emit the shared event schema.

6. Polish for demos.
   - Script the hero demo and repeatable demo scenarios.
   - Tune animation timing, glow, and color palettes for video.
   - Document wiring, flashing, the bill of materials, and running the agent so others can reproduce it.

## V2+ Architecture

The v1 host/device split should be the foundation for a longer-lived pet, not just a one-shot reaction renderer. Later versions should treat the model as a controller for state and behavior, not only as a source of mood labels. The V1 protocol envelope, event schema, and layered renderer are seeded specifically so these extensions do not force a rewrite.

### Persistent Pet State

- Keep the host as the canonical owner of long-term pet state.
- Model state should include fields such as mood, energy, hunger, attention, trust, boredom, recent interactions, and cooldowns.
- Device state should stay lightweight: current scene, current animation, short-lived effect timers, and the minimal mirror of host state needed for rendering.
- State changes should be event-driven and explicit so the model can intentionally cause decay, recovery, memory updates, and status shifts over time.

### Asynchronous Behaviors

- Separate "model produced an intent" from "pet is actually doing it now."
- Grow the V1 priority/replacement policy into a full behavior scheduler on the host that can queue, interrupt, defer, and resume behaviors.
- Behaviors should have triggers, durations, cooldowns, priorities, and optional preconditions.
- This is the layer that turns the pet from prebaked reactions into a creature that can continue acting after the original event has passed.

### Extensible Inputs

- Reuse the V1 normalized event schema; new input sources should only require a new adapter and schema mapping, not a redesign of the model contract.
- Add adapters for active app/window, terminal/build/test events, notifications, music/media, and later sensor inputs.
- Keep the model prompt centered on recent events plus the current pet state, rather than hardcoding source-specific logic everywhere.

### Graphics Abstraction

- Do not hardcode every arbitrary object as a firmware-native special case.
- Grow the V1 mood/action layering into a layered scene protocol so the host can express graphics as primitives, reusable named assets, or uploaded sprites.
- Recommended scene capabilities for later versions:
  - primitive shapes: line, rect, circle, polygon, text
  - reusable asset references: icons, symbols, recurring props
  - transforms: position, scale, rotation, flip
  - layers: foreground, background, effect layers
  - optional animation keyframes for short sequences
- A "house" should usually be composed from primitives or a reusable asset, not baked as a one-off firmware behavior.
- Only use raw frame streaming as an escape hatch for special cases; it should not be the default graphics path.

### Host/Model Strategy

- Keep backend, model family, and preset separate so the same control logic can run against Ollama, llama.cpp, LM Studio, or MLX-backed runtimes.
- Default family remains `qwen3.5`.
- Default preset remains `qwen3.5:4b`, with `qwen3.5:2b` as the low-memory fallback.
- Prefer MLX-backed variants on macOS when they materially improve responsiveness or memory use on Apple Silicon.
- Consider the multimodal/screenshot path as a richer context source once latency is managed.
- Preserve the ability to swap to another family later if a better small model appears.

## Testing And Acceptance

Firmware checks:

- `cargo build` from `device/` for the ESP32-C3 target.
- Parser/state tests in host-compatible modules where possible.
- Manual serial tests with valid, malformed, oversized, and rapid-fire commands.
- Hardware smoke tests for SSD1351 init, color output, cube orientation, and animation rate.

Renderer/iteration checks:

- Run the renderer in `embedded-graphics-simulator` on desktop for fast animation iteration.
- Snapshot tests of key moods/actions in CI where feasible.

Agent checks:

- Unit tests for model-output extraction, validation, event mapping, serial framing, and reconnect behavior.
- Tests for the reflex layer: events map to the correct instant reaction independent of the model.
- Mock Ollama responses for deterministic test runs.
- Dry-run mode that prints exactly what would be sent to the ESP.

V1 acceptance criteria:

- The pet boots into a lively idle animation, as a recognizable character, with no host connected.
- Transport: a serial reaction changes mood/action on screen in well under one second.
- Responsiveness: a salient event triggers a reflex reaction near-instantly, before any model-driven personality arrives.
- Robustness: bad model output never crashes the agent or firmware.
- The hero demo: a 15 to 30 second video clearly communicates that a holographic creature is reacting to computer events.

## Open Questions For Later

- Which creature concept and name to commit to for V1.
- Whether to keep `embedded-graphics`-compatible rendering (for the simulator) or a leaner raw framebuffer, if not decided in Phase 0.
- Whether the multimodal/screenshot input path is worth its latency and memory cost.
- Whether Wi-Fi should become a first-class V2 transport or remain optional.
- Which persistent pet state fields should be present in the first long-term version.
- Which input adapters should ship first beyond computer context.
- Whether on-device audio (piezo/speaker) is worth adding after host-driven audio.
- Whether the host needs a desktop control UI or remains CLI/service-first.
- Whether later graphics should stop at scene primitives and sprites or add a richer animation timeline.
