# esp-hologotchi

Hologotchi is a tiny holographic desk pet: an **ESP32-C3** drives a **128x128 SSD1351 RGB OLED** viewed through a dichroic cube, while a small **Python host service** decides what the pet should feel and do.

V1 is intentionally narrow:

- one pet: **Mochi**, a shiba-like holographic desk companion
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

See:

- [`PLAN.md`](PLAN.md) for the roadmap
- [`PET.md`](PET.md) for Mochi's locked personality and behavior vocabulary

## Repository layout

- `device/` — Rust firmware for the ESP32-C3
- `host/` — Python host service
- `PLAN.md` — product and implementation roadmap
- `PET.md` — pet identity, personality, and behavior contract

## How it works

The host is the brain. It asks the model what Mochi should do next, validates the result, and sends a single high-level behavior update to the device.

The device does not run the model. It:

- joins Wi-Fi
- listens for behavior updates on a TCP socket
- renders Mochi continuously on the OLED
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
> `device/hologotchi.local.toml` is read by `device/build.rs` at build time. If you change it, rebuild and reflash the firmware.

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

Send Mochi a direct message:

```bash
curl -X POST http://127.0.0.1:8787/message \
  -H 'Content-Type: application/json' \
  -d '{"text":"Mochi, I finally fixed the bug"}'
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

The HTTP response includes the queued input ID:

```json
{"ok":true,"id":"direct-1"}
```

When `--serve` is enabled, the host logs input and model-result records to stderr
so direct messages are distinguishable from regular idle loop updates:

```json
{"type":"input","status":"accepted","id":"direct-1","source":"direct_message","transport":"http","remote":"127.0.0.1","event":"Direct user message: Mochi, I finally fixed the bug"}
{"type":"behavior_result","input_id":"direct-1","source":"direct_message","animation":"happy","mood":"happy","text":"tail wag","alert":false,"duration_ms":3000}
```

Build and test result IDs use `build-*` and `test-*`; important alerts use
`alert-*`.

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
- `--serve` — expose `POST /message`, `/build`, `/test`, and `/alert` while the pet loop is running
- `--message-bind-host 127.0.0.1` — bind host for the message endpoint; use `0.0.0.0` for LAN clients
- `--message-port 8787` — bind port for the message endpoint

## Verification

### Device

Run from `device/`:

```bash
cargo fmt --all -- --check
cargo build
cargo clippy --all-features --workspace -- -D warnings
```

### Host

Run from the repository root:

```bash
uv run python -m unittest discover -s host/tests -t .
```

## Notes

- Runtime control is **Wi-Fi only** for V1. USB is for power/flashing, not runtime commands.
- The device is the **TCP server**; the host connects to the device by IP/hostname.
- The renderer is designed to stay `no_std` friendly and predictable on the ESP32-C3.
