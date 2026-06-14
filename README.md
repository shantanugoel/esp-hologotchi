# esp-hologotchi

Hologotchi is a tiny holographic desk pet: an **ESP32-C3** drives a **128x128 SSD1351 RGB OLED** viewed through a dichroic cube, while a small **Python host service** decides what the pet should feel and do.

V1 is intentionally narrow:

- one pet: **Shiro**, a Shiro-style holographic desk companion
- one runtime transport: **Wi-Fi**
- one control path: **newline-delimited JSON over a TCP connection**
- one host stack: **Python + Ollama**

## Current status

The repository currently includes:

- device firmware that boots into a local idle animation with no host attached
- Wi-Fi bring-up and reconnect handling on the ESP32-C3
- a TCP control socket on the device for behavior updates
- a Python host CLI that can either send one behavior, run a small stateful pet loop,
  or accept direct messages, build/test results, and one important alert path
  through a local HTTP endpoint
- a host-only psychology layer: real-time needs/relationship decay, an
  away/ignoring/engaged presence state machine, and local SQLite memory with
  inspect/forget/reset/pause controls
- physical **touch** via an on-device TTP223 pad: the firmware classifies
  tap/hold/doubletap and streams them to the host over a device → host uplink on
  the same TCP connection (with an HTTP `/touch` fallback for testing)

See:

- [`PLAN.md`](PLAN.md) for the roadmap
- [`PET.md`](PET.md) for Shiro's locked personality and behavior vocabulary
- [`DEMO.md`](DEMO.md) for the first demo shot list and trigger commands

## Repository layout

- `device/` — Rust firmware for the ESP32-C3
- `host/` — Python host service
- `PLAN.md` — product and implementation roadmap
- `PET.md` — pet identity, personality, and behavior contract

## How it works

The host is the brain. It asks the model what Shiro should do next, validates the result, and sends a single high-level behavior update to the device.

The device does not run the model. It:

- joins Wi-Fi
- listens for behavior updates on a TCP socket
- renders Shiro continuously on the OLED
- falls back to a local idle loop if the host is unavailable

Example wire payload:

```json
{"v":1,"kind":"behavior","mood":"happy","animation":"happy","text":"good build","alert":false,"duration_ms":4000}
```

## Prerequisites

### Device

- Rust toolchain with target `riscv32imc-unknown-none-elf`
- `espflash`
- ESP32-C3 hardware wired to the SSD1351 display

### Host

- Python 3.10+
- [`uv`](https://docs.astral.sh/uv/)
- [Ollama](https://ollama.com/) running locally

Default model settings are:

- family: `qwen3.5`
- preset: `qwen3.5:4b`
- fallback: `qwen3.5:2b`

## Device setup

Device Wi-Fi and control-socket settings are kept in a **local, git-ignored TOML file**.

1. Copy the example config:

   ```bash
   cp device/hologotchi.example.toml device/hologotchi.local.toml
   ```

2. Edit `device/hologotchi.local.toml` and set:

   - `wifi.ssid`
   - `wifi.password`
   - `control.port` if you want something other than `4242`

3. Build from `device/`:

   ```bash
   cd device
   cargo build
   ```

4. Flash and monitor:

   ```bash
   cargo run
   ```

`device/.cargo/config.toml` already sets the runner to `espflash flash --monitor --chip esp32c3`.

> [!IMPORTANT]
> `device/hologotchi.toml` is read by `device/build.rs` at build time. If you change it, rebuild and reflash the firmware.

## Touch hardware (TTP223)

Shiro's first physical input is a **TTP223 capacitive touch module**. The
firmware samples it, classifies `tap` / `hold` / `doubletap` with a deterministic
debounced state machine, and sends each gesture to the host as a device → host
`input` frame on the same TCP connection used for behavior updates.

Wiring (default pin `GPIO5`, which avoids the OLED pins GPIO2/3/4/6/7 and the
ESP32-C3 strapping pins GPIO2/8/9):

```text
TTP223 VCC -> ESP32-C3 3V3
TTP223 GND -> ESP32-C3 GND
TTP223 OUT -> ESP32-C3 GPIO5   (active-high momentary)
```

Notes:

- Power the module from **3.3V** so the OUT level is safe for the ESP32-C3.
- The OUT polarity is the TTP223 default: **active-high momentary**. The firmware
  configures an internal pull-down so the line reads low if the pad is ever
  disconnected.
- To use the documented backup pin `GPIO10` instead, build with the
  `touch-gpio10` Cargo feature: `cargo build --features touch-gpio10`.
- Confirm your exact board's pinout before committing a pad placement; boards
  vary on straps, onboard LEDs, and USB/JTAG routing.

The uplink frames are minimal, newline-delimited JSON (the host timestamps them
on receipt):

```json
{"v":1,"kind":"input","source":"touch","gesture":"tap"}
{"v":1,"kind":"input","source":"touch","gesture":"hold","duration_ms":960}
{"v":1,"kind":"input","source":"touch","gesture":"doubletap"}
```

If the host link is down when you touch the cube, the device plays a brief
local-only acknowledgement (a blink or look-around) and nothing durable changes;
those touches are not replayed when the host reconnects.

The Wokwi diagram (`device/diagram.json`) includes a pushbutton on `GPIO5` that
emulates the active-high TTP223, so tap/hold/doubletap can be exercised in
simulation.

## Host setup with uv

The host currently uses only the Python standard library at runtime, but a root `pyproject.toml` is included so `uv` can create an environment and install the local CLI cleanly.

From the repository root:

1. Create the environment and install the local package:

   ```bash
   uv sync
   ```

2. Make sure Ollama is running, and pull the default model if needed:

   ```bash
   ollama pull qwen3.5:4b
   ```

3. Send one behavior:

   ```bash
   uv run hologotchi-host --device-host 192.168.1.50 "the build passed"
   ```

You can also run the module form directly:

```bash
uv run python -m host --device-host 192.168.1.50 "the build passed"
```

Run the Phase 5 pet loop:

```bash
uv run hologotchi-host --device-host 192.168.1.50 --loop
```

Run the pet loop with the Phase 6 direct-message endpoint:

```bash
uv run hologotchi-host --device-host 192.168.1.50 --loop --serve
```

Send Shiro a direct message:

```bash
curl -X POST http://127.0.0.1:8787/message \
  -H 'Content-Type: application/json' \
  -d '{"text":"Shiro, I finally fixed the bug"}'
```

Report a build or test result:

```bash
curl -X POST http://127.0.0.1:8787/build \
  -H 'Content-Type: application/json' \
  -d '{"ok":true,"text":"cargo build completed"}'

curl -X POST http://127.0.0.1:8787/test \
  -H 'Content-Type: application/json' \
  -d '{"ok":false,"text":"host tests failed"}'
```

Send the one V1 important alert path:

```bash
curl -X POST http://127.0.0.1:8787/alert \
  -H 'Content-Type: application/json' \
  -d '{"text":"calendar event starts now"}'
```

Shiro feels physical touch through a **TTP223 capacitive pad on the device** (see
[Touch hardware](#touch-hardware-ttp223) below): the firmware classifies the
gesture and sends it to the host over the existing connection automatically. The
same `POST /touch` endpoint stays available for testing without hardware and for
future remote-touch sources. Valid gestures are `tap`, `hold`, and `doubletap`;
`duration_ms` is optional and only meaningful for a `hold`:

```bash
curl -X POST http://127.0.0.1:8787/touch \
  -H 'Content-Type: application/json' \
  -d '{"gesture":"hold","duration_ms":1200}'
```

A `tap` is a light boop, a `hold` is a soothing pet (repairs affection, calms an
alert), and a `doubletap` is a play invite Shiro takes up when it has the energy.
Touch counts as engagement, wakes Shiro from a nap through a gentle waking
transition, and tags salient affection moments in memory. Whether a gesture
arrives from the device or from HTTP, the host reacts identically.

The HTTP response includes the queued input ID:

```json
{"ok":true,"id":"direct-1"}
```

When `--serve` is enabled, the host logs input and model-result records to stderr
so direct messages are distinguishable from regular idle loop updates:

```json
{"type":"input","status":"accepted","id":"direct-1","source":"direct_message","transport":"http","remote":"127.0.0.1","event":"Direct user message: Shiro, I finally fixed the bug"}
{"type":"behavior_result","input_id":"direct-1","source":"direct_message","animation":"happy","mood":"happy","text":"tail wag","alert":false,"duration_ms":3000}
```

Build and test result IDs use `build-*` and `test-*`; important alerts use
`alert-*`; touch gestures use `touch-*`.

The input endpoint binds to localhost by default. To accept inputs from other
machines on the local network, bind it explicitly:

```bash
uv run hologotchi-host --device-host 192.168.1.50 --loop --serve --message-bind-host 0.0.0.0
```

LAN clients should post to the host machine's actual IP address, for example
`http://192.168.1.20:8787/message`.

Useful flags:

- `--device-port 4242` — override the TCP port if you changed it on the device
- `--ollama-url http://127.0.0.1:11434` — override the Ollama base URL
- `--model-preset qwen3.5:2b` — use the lower-memory preset
- `--ollama-keep-alive 30m` — ask Ollama to keep the model loaded between requests; use `-1` to keep it loaded indefinitely
- `--dry-run` — print the validated JSON payload without sending it
- `--loop` — keep in-memory pet state and send repeated behavior updates
- `--interval-seconds 6` — control the loop cadence
- `--max-cycles 10` — run a bounded loop for demos or tests
- `--serve` — expose `POST /message`, `/build`, `/test`, `/alert`, and `/touch` while the pet loop is running
- `--message-bind-host 127.0.0.1` — bind host for the message endpoint; use `0.0.0.0` for LAN clients
- `--message-port 8787` — bind port for the message endpoint
- `--memory-db PATH` — where Shiro keeps its local SQLite memory (defaults under `$XDG_STATE_HOME/hologotchi/`)
- `--no-memory` — run the loop without persisting or recalling memory
- `--reset-memory` / `--inspect-memory` — erase or print the local memory store, then exit
- `--away-idle-seconds 300` — OS idle time at which the owner counts as away
- `--engaged-window-seconds 90` — how long after a direct interaction Shiro still feels engaged
- `--focus-jealousy-seconds 1200` — heads-down time on one app before Shiro gets a little jealous

## Shiro's mind: needs, presence, and memory

When the loop runs it keeps a small but persistent inner life (host-only; the
device firmware and wire protocol are unchanged):

- **Needs and relationship (Phase 9a):** `social`, `play`, `rest`, and
  `stimulation` drives decay in real wall-clock time and are replenished by your
  attention and Shiro's own actions. Neglect escalates content → restless →
  needy → sad/grumpy → withdrawn, always recoverable through attention, play,
  praise, rest, or an apology. A slow `bond` level grows over days.
- **Presence (Phase 9b):** Shiro distinguishes *away* (you're gone or the screen
  is locked — not rejection) from *present-but-ignoring* (you're at the computer
  but not interacting — real "ignored") from *engaged*. Feed cheap, opt-in,
  local signals to the loop:

  ```bash
  curl -X POST http://127.0.0.1:8787/presence \
    -H 'Content-Type: application/json' \
    -d '{"idle_seconds":12,"screen_locked":false,"foreground_app":"editor"}'
  ```

  With no signals posted, Shiro assumes you are **away** (a benign absence, never
  treated as rejection), so "ignored" and jealousy only kick in once a helper
  starts reporting presence. Post the **full** signal set each time — each
  `POST /presence` replaces the previous one. Long heads-down focus on a single
  app while ignoring Shiro produces a grounded touch of jealousy.
- **Memory (Phase 9c):** meaningful moments are scored for salience and stored in
  local SQLite (recent messages, praise, ignored stretches, build/test outcomes,
  alerts). A bounded, ranked set is recalled into each prompt. Memory is fully
  local and private, with a versioned schema and thread-safe access.

  ```bash
  curl http://127.0.0.1:8787/memory                                  # inspect
  curl -X POST http://127.0.0.1:8787/memory/forget -d '{"id":3}'      # forget one
  curl -X POST http://127.0.0.1:8787/memory/forget -d '{"tag":"alert"}'
  curl -X POST http://127.0.0.1:8787/memory/reset  -d '{}'           # wipe all
  curl -X POST http://127.0.0.1:8787/memory/writes -d '{"enabled":false}'  # pause writes
  ```

  New feelings ship with **no firmware change**: sadness, neediness, grumpiness,
  and jealousy are expressed through the existing 11 animations (see `PET.md`).

### Device

Run from `device/`:

```bash
cargo fmt --all -- --check
cargo build
cargo clippy --all-features --workspace -- -D warnings
```

The pure gesture classifier (`device/touch`) has host-runnable unit tests. Run
them from the repository root so the device's `riscv` cargo config is not picked
up:

```bash
cargo test --manifest-path device/touch/Cargo.toml
```

### Host

Run from the repository root:

```bash
uv run python -m unittest discover -s host/tests -t .
```

## Notes

- Runtime control is **Wi-Fi only** for V1. USB is for power/flashing, not runtime commands.
- The device is the **TCP server**; the host connects to the device by IP/hostname. The
  connection is bidirectional: behavior frames flow host → device and touch `input`
  frames flow device → host on the same socket.
- The renderer is designed to stay `no_std` friendly and predictable on the ESP32-C3.
