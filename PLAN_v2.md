# Hologotchi Plan V2: Real Pet Presence and Touch

## Goal

Turn Mochi from a good host-driven virtual pet into a pet that feels physically
present on the desk:

- it notices when the owner is probably nearby or away
- it reacts immediately to touch
- it sleeps, wakes, gets drowsy, gets lonely, plays, and settles with continuity
- it uses memory and personality without drifting into random per-tick behavior
- it keeps the ESP32-C3 simple: sensing, rendering, Wi-Fi transport, bounded state

The V2 mental model is:

> The LLM is Mochi's pet mind. The host is Mochi's body and nervous system. The
> ESP32-C3 is Mochi's face and simple physical sensor hub.

The LLM should not be reduced to a text/emotion generator. It should decide what
Mochi wants, feels, tries, and expresses. The host still owns durable facts and
physical continuity: whether Mochi has been sleeping for 8 minutes, whether the
owner just returned, whether a touch happened now, and whether a transition is
believable.

## Non-Negotiables

- Keep model inference, planning, memory, and interpretation on the host.
- Keep runtime transport Wi-Fi only.
- Keep one pet, one connection, one control loop.
- Keep the device behavior schema and the closed mood/animation vocabulary
  unchanged. No new animations for V2; body state maps to existing animations.
- The device may classify touch *gestures*, never their *meaning*.
- Do not build a generic adapter/event/scene framework. Reuse `HostInputQueue`
  and a few explicit source strings.
- Do not move to ESP32-S3 for touch/presence alone. ESP32-C3 remains fine.
- Firmware remains no_std-friendly: fixed buffers, bounded JSON, deterministic
  debouncing, rate-limited input frames, drop-on-full queues.

## Milestones

V2 is split into three incremental milestones so the new reverse channel
(device -> host) and the LLM/state changes are de-risked separately. Each
milestone has its own acceptance gate and is shippable on its own.

### V2a - Presence + body continuity (host-only)  [effort: M]

No firmware changes. Prove the "feels alive and coherent" behavior with the
existing one-way downlink.

- Extend `/presence` payloads with `present`, `source`, `ttl_seconds`.
- Merge presence by source with per-source TTL/expiry.
- Make meaningful presence changes wake the loop immediately.
- Add a minimal body-state model (`awake | drowsy | sleeping | waking`) with
  sleep inertia, coupled to existing affect drives.
- Tighten prompt construction (body state, allowed transitions, allowed
  animations, local time, relevant memories).
- Add a separate host-only model proposal parser so the model may return
  optional `intent` / `body_state` without breaking the strict device parser.
- Add the OLED burn-in guardrail and the keepalive (both are pure host/firmware
  hygiene; keepalive is a bare-newline no-op the device already ignores).

**Gate:** Demo moments 1-3 and 6-7 work using `/message`, `/alert`, and
`/presence` only (no touch hardware).

### V2b - Touch via host HTTP first  [effort: M]

Still no firmware changes. Validate all touch *meaning* before any hardware.

- Add `POST /touch` and `HostInputQueue.submit_touch(...)`.
- Add `touch` to the engagement sources.
- Add deterministic touch effects on affect/body state.
- Add touch memory tags for salient affection moments.

**Gate:** Demo moments 4-7 work by POSTing touch gestures over HTTP.

### V2c - Firmware touch + bidirectional TCP uplink  [effort: L]

Only now touch the firmware and the wire.

- Add a pure, deterministic gesture classifier (host-testable logic).
- Add a bounded firmware event queue (drop-oldest when full).
- Add device -> host uplink frames on the existing persistent TCP connection.
- Add a host transport reader on the same socket; protect send/close/reconnect.
- Add a bounded local touch acknowledgement when the host link is down.
- Wire the TTP223 into real hardware last, after a per-board pin check.

**Gate:** Touch on the physical cube produces the V2b behavior end to end, and
rendering/Wi-Fi/local-idle survive disconnects and repeated downlinks.

## Architecture

### Layer 1: Signals

Raw observations from optional helpers or device hardware:

- AirPods connected/disconnected
- host idle/screen-locked/foreground app
- TTP223 touch tap/hold/doubletap
- direct messages
- build/test result
- important alert
- local time

Signals are not emotions. They are facts.

### Layer 2: Host-Owned Pet State

Durable truth the host updates deterministically. Most of this already exists in
`host/affect.py` and `host/presence.py`; V2 adds body state and presence fusion
on top, it does not re-implement the drives.

- presence: `away`, `present_but_ignoring`, `engaged` (exists)
- affect/drives: social, play, rest, stimulation, energy, sleepiness (exists)
- relationship: affection, trust, loneliness, frustration, bond (exists)
- recent events and memories (exists)
- body state: `awake`, `drowsy`, `sleeping`, `waking` (new, V2a)
- elapsed durations: time away, time present, time ignored, time asleep

This state provides inertia. It prevents deep sleep -> zoomies -> sad -> nap
within a few idle ticks unless real events justify it.

> Coupling note: body state is a thin discrete layer over the continuous affect
> drives. `sleepiness`/`energy`/`rest` already live in `Affect`. Body state must
> read those drives (do not invent a second sleepiness model), and entering
> `sleeping` must keep affect consistent (e.g. a real nap calls
> `Affect.register_behavior("nap")` so energy recovers and sleepiness falls).
> Contradictions like "sleeping with energy 100 and sleepiness 0" are not allowed.

### Layer 3: LLM Pet Mind

The LLM receives a compact state summary and chooses Mochi's next action. It may
propose:

- mood
- animation
- short text
- intent (host-only)
- next body state (host-only)
- duration

The LLM is allowed to decide things like:

- "Mochi half-wakes because the owner returned."
- "Mochi stays asleep because it is late and no one touched it."
- "Mochi acts a little pouty because the owner was present but ignored it."
- "Mochi wants to play after a doubletap."
- "Mochi calms down because it was petted during an alert."

The host validates the proposal against continuity rules. Invalid transitions are
softened, not treated as fatal.

Example:

```text
Current body_state: sleeping
Sleeping for: 90 seconds
Minimum nap: 5 minutes
Trigger: idle tick
LLM proposes: awake + excited
Host result: soften to sleeping/drowsy, or ask for a constrained fallback
```

But:

```text
Current body_state: sleeping
Trigger: touch hold
LLM proposes: waking + happy
Host result: accept
```

## Wire Protocol

Keep the current behavior downlink unchanged:

```json
{"v":1,"kind":"behavior","mood":"sleepy","animation":"nap","text":"zzz","alert":false,"duration_ms":8000}
```

Add one uplink message family on the same single TCP connection. Frames are
intentionally minimal; the host timestamps events on receipt, so no device clock
field is sent:

```json
{"v":1,"kind":"input","source":"touch","gesture":"tap"}
{"v":1,"kind":"input","source":"touch","gesture":"hold","duration_ms":1200}
{"v":1,"kind":"input","source":"touch","gesture":"doubletap"}
```

Rules:

- `behavior` frames are host -> device.
- `input` frames are device -> host.
- Both are newline-delimited JSON.
- Firmware writes only small bounded input frames.
- Malformed uplink frames are ignored by the host.
- Malformed downlink frames are ignored by firmware.
- Input frames must never block display rendering.

## Bidirectional Transport

This is the highest-risk new piece, because today the channel is one-way. Ground
truth in the current code:

- The device is the TCP **server**: `behavior_server_task` calls
  `socket.accept(control_port)` and then `read_control_stream`, which loops on
  `socket.read(...)` and never writes (`device/src/bin/main.rs`).
- The host is the TCP **client**: `BehaviorClient` only has `send(...)`; there is
  no reader (`host/transport.py`).
- The device's socket inactivity timeout is 30s
  (`SOCKET_TIMEOUT_SECS`), while the host loop's adaptive idle interval can reach
  60s during away/nap (`_adaptive_interval` in `host/pet_loop.py`). Left as-is,
  the connection drops mid-nap and uplink touch cannot arrive promptly.

### Connection lifecycle

- The host owns one persistent socket for the life of the loop and reconnects on
  failure (already true for sends; extend to the reader).
- The device accepts one connection, services it until close, then loops back to
  `accept`. It cannot initiate, so the host is responsible for keeping the link
  alive and for reconnecting.

### Keepalive (guardrail)

- The host sends a bare `\n` keepalive whenever it would otherwise be idle longer
  than ~15s (comfortably under the 30s device timeout). This keeps the socket
  open so uplink touch is delivered immediately.
- This is free on the firmware side: an empty line already parses to
  `ParseError::Empty` and is ignored, so no firmware change is needed to accept
  it. Optionally also enable TCP keepalive on the host socket.

### Host reader

- Add a dedicated reader thread that `recv`s from the same socket, splits on
  `\n`, parses bounded `input` frames, and calls
  `HostInputQueue.submit_touch(...)`.
- Guard the socket with a lock so the sender, the reader, and reconnect logic do
  not race on close/reconnect. After a reconnect, the reader re-attaches to the
  new socket.

### Firmware multiplex

- The socket task must read downlink frames **and** drain a bounded input queue
  to write uplink frames, without one starving the other.
- Preferred: `TcpSocket::split()` into reader/writer halves and run the two
  futures concurrently (`join`). Verify `split()` exists in the pinned
  `embassy-net`; if not, multiplex one task with `embassy_futures::select` over
  `socket.read(...)` and an input-queue signal, using a short read timeout.
- Treat the firmware-multiplex approach as a small spike to confirm against the
  pinned embassy version before building on it.

### Degraded mode (host disconnected)

- If the host is connected, send touch input to the host as usual.
- If the host is disconnected, the firmware may play a **bounded local
  acknowledgement only** (a brief `blink`/`look_around`). It must not update
  durable mood or memory on the device, and it must not interpret meaning.
- Touch events captured while disconnected are dropped by the bounded queue
  (drop-oldest); they are not replayed when the host returns.

## Presence

### General Presence Signal

Presence is source-agnostic. AirPods are the first explicit source, but the loop
must not know AirPods-specific details. `host/presence.py` already classifies
`engaged | present_but_ignoring | away` from host-activity signals; V2 adds an
explicit presence source and fuses the two.

Preferred `/presence` payload (explicit-presence source):

```json
{
  "present": true,
  "source": "airpods",
  "ttl_seconds": 30
}
```

Disconnected:

```json
{
  "present": false,
  "source": "airpods",
  "ttl_seconds": 30
}
```

Also keep the existing host-activity fields (posted by a separate helper):

```json
{
  "idle_seconds": 12,
  "screen_locked": false,
  "foreground_app": "Code"
}
```

> `confidence` is intentionally omitted in V1: nothing consumes it yet. Add it
> only if a real fusion rule needs it.

### Multi-source fusion and TTL

There are now (at least) two independent posters to `/presence`: the AirPods
helper and the host-activity helper. The current `SignalMailbox` is single-slot
"latest wins", so two posters would clobber each other. Fix:

- Store presence by **source key**, each with its payload and a `received_at`
  stamp. A new AirPods post must not erase the latest host-activity fields and
  vice versa.
- Expire a source's contribution once `now - received_at > ttl_seconds`.
- The pet loop reads the fused view once per tick (as it does today).

### Presence precedence (classification order)

Conflicts are resolved in this fixed order (decision: an explicit AirPods signal
is authoritative for absence):

1. Recent direct message or touch -> `engaged` (an explicit interaction always
   wins; you can interact without AirPods).
2. Fresh explicit presence `present=false` -> `away` (authoritative, even if
   local input looks active). *Trade-off accepted: leaving without AirPods, or
   leaving them on the desk, can misread; chosen for simplicity and because the
   owner usually wears them while present.*
3. Fresh explicit presence `present=true` and no recent interaction ->
   `present_but_ignoring`.
4. No fresh explicit presence: fall back to host activity — screen locked or idle
   past threshold -> `away`; unlocked/active -> `present_but_ignoring`.
5. Nothing fresh at all -> treat as benign absence (`away`), never as rejection.

### Immediate presence reaction

Today a presence POST cannot wake a loop that is sleeping up to 60s. Fix:

- On a meaningful raw change (e.g. `present` flips, or a source newly
  appears/expires), `/presence` enqueues a lightweight `presence_signal` event
  into `HostInputQueue` so the loop wakes now.
- The loop reclassifies, and for `presence_signal` events it builds the situation
  text from the transition rather than the generic self-directed describer:

```text
Presence changed: owner returned after 37 minutes away.
Presence changed: owner left.
Presence changed: owner is nearby but has not interacted for 15 minutes.
```

- Add transition flags to `PresenceReport` as needed (it already exposes
  `returned_from_away`; add a `just_left` equivalent).
- Coalesce duplicate `presence_signal` events so rapid posts cannot spam the loop.

### AirPods Helper (after core presence)

Build this only after the `/presence` fusion, TTL, and immediate-event logic are
done and tested. Bluetooth probing is brittle and platform-specific, so it must
not gate the core behavior.

```bash
python -m host.airpods_presence --name "Shantanu's AirPods" --url http://localhost:8787/presence
```

Responsibilities:

- poll Bluetooth state every 5-10 seconds
- post `present=true` when the named AirPods are connected
- post `present=false` when disconnected
- include `ttl_seconds`
- debounce changes; require 2 consecutive readings before flipping
- log only connection state and POST errors
- post only to localhost by default

Implementation options:

- macOS first: `blueutil` if available
- macOS fallback: `system_profiler SPBluetoothDataType -json`
- Linux later: `bluetoothctl`

The helper should not know about Mochi's feelings. It only posts presence.

## Touch

### Hardware

Use a TTP223 capacitive touch module for the first physical input.

Recommended wiring:

```text
TTP223 VCC  -> ESP32-C3 3V3
TTP223 GND  -> ESP32-C3 GND
TTP223 OUT  -> ESP32-C3 GPIO5
```

Current display pin use:

```text
GPIO4 = OLED SCK
GPIO6 = OLED MOSI
GPIO7 = OLED CS
GPIO3 = OLED DC
GPIO2 = OLED RST
```

Avoid the display pins. `GPIO5` is the preferred first touch input; `GPIO10` is a
reasonable backup. Before committing:

- confirm the exact ESP32-C3 board pinout (boards vary on straps, onboard LEDs,
  USB/JTAG routing, exposed pins)
- confirm the chosen pin is free at boot and not a strapping pin on this board
- document the TTP223 OUT polarity (default active-high momentary) used by the
  firmware
- make the touch pin a build-time/config constant, not a permanently hardcoded
  literal

Power the TTP223 from 3.3V so the OUT signal is safe for the ESP32-C3.

For enclosure placement:

- mount the touch pad behind the case where a user naturally taps/pets the cube
- 2mm PLA is likely workable with a reasonably large pad
- use copper foil around 20-30mm square if the module's onboard pad is unreliable
- keep the pad away from metal, USB shields, display flex, and long SPI runs
- prototype sensitivity before committing the case

### Firmware Gesture Classifier

A pure, deterministic state machine with explicit, testable semantics:

- debounce: require a stable level for ~30-50ms before accepting an edge
- press shorter than 500ms is a tap candidate
- press in 500-699ms is treated as a tap (one band only; no undefined gap)
- press at least 700ms is a `hold`: emit once when the threshold is crossed, and
  report the final `duration_ms` on release
- a tap is emitted only after the doubletap window (350-450ms inter-tap)
  expires; if a second tap arrives inside the window, emit `doubletap` instead
- rate-limit events (e.g. at most ~5/sec) and use a small fixed-size queue
- keep rendering independent from touch sampling

Example uplink frames:

```json
{"v":1,"kind":"input","source":"touch","gesture":"tap"}
{"v":1,"kind":"input","source":"touch","gesture":"hold","duration_ms":960}
{"v":1,"kind":"input","source":"touch","gesture":"doubletap"}
```

### Host Touch Meaning

Touch is real engagement. Add `touch` to the engagement sources and give each
gesture a deterministic effect on affect/body state via the existing `Affect`
methods (e.g. attention/soothe/play replenishment), not new ad-hoc fields.

- `tap`: boop, attention, light wake-up
- `hold`: petting, soothing, affection repair, alert acknowledgment
- `doubletap`: play invite (if energy allows)

Host-side event text examples:

```text
Touch input: boop tap.
Touch input: gentle pet hold for 1200ms.
Touch input: double boop play invite.
```

Touch should wake the loop immediately and tag salient affection moments in
memory. In V2b this is exercised entirely through `POST /touch`; V2c only swaps
the source from HTTP to the firmware uplink.

## Body State

Add a small body-state model to `host/body.py` (or `host/state.py` if it stays
tiny). Keep it minimal and derive sleep pressure from existing affect.

```python
class BodyState(str, Enum):
    AWAKE = "awake"
    DROWSY = "drowsy"
    SLEEPING = "sleeping"
    WAKING = "waking"
```

Track only what the rules need:

```text
body_state
body_state_since
sleep_started_at
last_touch_at
```

Persist `body_state` and its timestamps when memory is enabled, so a long nap
survives a host restart instead of resetting to `awake`.

Deterministic body rules:

- Away is not rejection. If owner is away, Mochi calms down, gets drowsy, and may
  sleep.
- Present but ignored increases loneliness/social need over time.
- Owner return after meaningful absence creates a reunion opportunity.
- Sleep has inertia. Mochi should usually stay asleep for a minimum nap duration.
- Touch, important alert, or meaningful owner return can interrupt sleep.
- Waking should usually pass through `waking` or `drowsy`, not instant excitement.
- Late night increases sleep pressure.
- A hold touch can soothe Mochi without forcing play.
- A doubletap can invite play if Mochi has enough energy.
- Entering `sleeping`/`drowsy` keeps affect consistent (recover energy, lower
  sleepiness via the existing nap behavior hook).

Initial thresholds:

```text
engaged window: 90s
owner return threshold: 2-3 min away
ignored needy threshold: 10-15 min
self-nudge threshold: 30-45 min
drowsy before sleep: 2-5 min
minimum nap duration: 5-10 min unless touch/alert/meaningful return
tap threshold: < 500ms
hold threshold: >= 700ms
doubletap window: 350-450ms
```

## LLM Output Shape

Keep the device behavior schema unchanged. The current strict parser
(`host/protocol.py` `parse_behavior_response`) rejects any unknown field, so the
model cannot simply add `intent`/`body_state` to a `behavior` frame.

Introduce a separate host-only proposal type, e.g. `BehaviorProposal`:

- required behavior fields (validated with the existing rules)
- optional `intent` (validated against a small closed list)
- optional `body_state` (validated against allowed transitions)
- `.to_behavior_command()` that strips the host-only fields and returns the
  existing `BehaviorCommand` for the device downlink

Model response candidate:

```json
{
  "v": 1,
  "kind": "behavior",
  "mood": "sleepy",
  "animation": "nap",
  "text": "zzz",
  "alert": false,
  "duration_ms": 9000,
  "intent": "stay_asleep",
  "body_state": "sleeping"
}
```

Another candidate:

```json
{
  "v": 1,
  "kind": "behavior",
  "mood": "happy",
  "animation": "blink",
  "text": "you came back",
  "alert": false,
  "duration_ms": 4000,
  "intent": "soft_reunion",
  "body_state": "drowsy"
}
```

Validation:

- Validate behavior fields with the existing rules.
- Validate `intent` against a small optional list.
- Validate proposed `body_state` against allowed transitions.
- If extra fields are missing, infer from animation and current state.
- If a proposed transition is invalid, soften it to the nearest valid state and
  behavior.

Do not send `intent` or `body_state` to the ESP32-C3. The device only needs the
existing behavior frame.

## Prompt Contract

The prompt should tell the LLM what is physically true, what it may choose, and
what would be incoherent. Allowed animations must always come from the closed
vocabulary (`idle, blink, look_around, walk, happy, play, excited, sleepy, nap,
worried, alert`).

Sleeping idle example:

```text
Body:
- state: sleeping
- asleep for: 8 minutes
- minimum nap has elapsed: yes
- local time: 14:20

Presence:
- owner: away
- away for: 32 minutes

Current moment:
Quiet idle tick. No touch, no alert, no direct message.

Allowed body states:
- sleeping
- drowsy

Allowed animations:
- nap
- sleepy
- blink

Guidance:
Mochi may stay asleep, stir drowsily, or dream. Do not become fully excited
unless there is a strong reason.
```

Owner return example:

```text
Body:
- state: sleeping
- asleep for: 14 minutes
- local time: 18:05

Presence:
- owner just returned after 37 minutes away

Relevant memory:
- Yesterday, the owner petted Mochi awake after returning.

Allowed body states:
- sleeping
- waking
- drowsy
- awake

Allowed animations:
- nap
- sleepy
- blink
- look_around
- happy
- excited

Guidance:
This can be a meaningful reunion. Mochi can wake slowly, perk up, or be a little
dramatic, but should not ignore the return if it feels important.
```

Ignored while present example:

```text
Body:
- state: awake
- local time: 15:10

Presence:
- owner present but ignoring for 28 minutes
- foreground app: Code

Drives:
- social: 28/100
- play: 34/100
- loneliness: 58/100
- frustration: 18/100

Allowed body states:
- awake
- drowsy

Allowed animations:
- look_around
- walk
- play
- worried
- idle

Guidance:
Mochi wants attention but should not use alert. It may angle for play, act
lonely, patrol, or quietly sulk.
```

Touch hold example:

```text
Current moment:
Touch input: gentle pet hold for 1200ms.

Body:
- state: drowsy

Presence:
- owner engaged now

Guidance:
This is soothing affection. Mochi may relax, brighten softly, or wake a little.
Do not turn it into an alert.
```

## Immediate Events

These should interrupt the idle wait and run the pet loop immediately:

- direct message
- build/test result
- important alert
- touch tap/hold/doubletap
- presence changed to away
- presence changed to returned/present

The existing input queue already gives this shape. Extend it rather than adding a
second scheduler, and coalesce duplicate `presence_signal` events.

## Display Care (burn-in guardrail)

The render loop draws the full 128x128 framebuffer at 20fps continuously. A long
`nap` would otherwise hold a near-static pose for many minutes and risk SSD1351
burn-in. Add a lightweight guardrail (no transport optimization needed now):

- during long sleep/away, keep subtle micro-motion alive (breathing, occasional
  shift) so pixels are not perfectly static
- periodically shift the pose by a pixel or two, or dim/blank briefly
- keep the existing local idle animation running while disconnected
- consider lowering the frame rate during long sleep later, but do not optimize
  display transport now

## Demo Script

Implement V2 so these moments work reliably (1-3, 6-7 are V2a; 4-5 add in V2b/c):

1. Owner leaves: AirPods disconnect. Mochi looks around, settles, gets drowsy,
   then sleeps.
2. Owner returns: AirPods connect. Mochi wakes or perks up. If away long enough,
   it gives a reunion reaction.
3. Owner is present but ignoring: AirPods connected and no touch/message for a
   while. Mochi becomes needy or lonely and tries a small nudge.
4. Petting: hold touch. Mochi calms, brightens, or wakes gently.
5. Play invite: doubletap. Mochi chooses `play` or `excited` if energy allows.
6. Wake from nap: tap while sleeping. Mochi goes through waking/drowsy rather
   than instant chaos.
7. Soothe alert: alert appears; touch hold acknowledges and calms Mochi.

## Implementation Order

### V2a (host-only)

1. Extend `/presence` payloads with `present`, `source`, `ttl_seconds`; merge by
   source with `received_at` and TTL expiry (no more single-slot clobber).
2. Apply the presence precedence order and add `just_left`-style transition flags.
3. Make meaningful presence transitions enqueue an immediate `presence_signal`
   loop event; build transition situation text; coalesce duplicates.
4. Add the body-state model and transition validation, coupled to affect.
5. Tighten prompt construction (body state, allowed transitions, allowed
   animations, local time, relevant memories).
6. Add the `BehaviorProposal` parser for optional host-only `intent`/`body_state`;
   strip them before sending the device frame.
7. Add the keepalive (bare `\n` under the 30s device timeout) and the burn-in
   guardrail.

### V2b (host touch over HTTP)

8. Add `HostInputQueue.submit_touch(...)`, `POST /touch`, touch in
   `ENGAGEMENT_SOURCES`, touch affect/body effects, and touch memory tags.

### V2c (firmware touch + uplink)

9. Add TTP223 firmware input sampling and the deterministic gesture classifier.
10. Add device -> host uplink frames on the existing TCP connection (firmware
    multiplex via `split()` or `select`).
11. Add the host transport reader that parses uplink input frames and submits them
    to `HostInputQueue`; protect send/reader/reconnect.
12. Add the bounded local touch acknowledgement for the host-disconnected case.
13. Tune thresholds against the demo script.

## Host Modules (extend existing first)

Most of these already exist; prefer extending them over adding new files.

- `host/presence.py` (exists): source-keyed signals, TTL/expiry, precedence,
  transition flags, immediate-event reporting.
- `host/inputs.py` (exists): `submit_touch(...)` and validation.
- `host/transport.py` (exists): bidirectional socket — persistent connection,
  keepalive, reader, reconnect.
- `host/pet_loop.py` (exists): `presence_signal`/touch handling, body transition
  application, prompt flow.
- `host/state.py` (exists): prompt context and recent behavior tracking.
- `host/protocol.py` (exists): add the `BehaviorProposal` parser.
- `host/body.py` (new, only if body rules outgrow `state.py`): body state,
  transition rules, allowed animation sets.
- `host/airpods_presence.py` (new, V2a-after-core): optional platform helper.

## Firmware Changes (V2c)

- Add TTP223 input pin config as a build-time constant, initially `GPIO5`.
- Add a debounced touch sampler.
- Add the deterministic tap/hold/doubletap classifier (pure, host-testable).
- Add a small bounded queue for input events (drop-oldest when full).
- Update the control socket task to write queued input frames while still reading
  behavior frames (multiplex).
- Add a bounded local acknowledgement when no host is connected.
- Keep local idle rendering alive (with burn-in micro-motion) if the host
  disconnects.

## Tests

Host tests:

- `/presence` accepts `present`/`source`/`ttl_seconds` payloads.
- two sources (AirPods + host activity) do not clobber each other; each expires
  on its own TTL.
- presence precedence resolves conflicts as specified (AirPods `present=false` ->
  away even with active local input; recent touch -> engaged).
- expired presence falls back safely.
- AirPods helper debounces before posting changed state.
- presence state changes enqueue immediate `presence_signal` events; duplicates
  coalesce.
- `/touch` validates gestures and enqueues touch events.
- touch counts as engagement and applies the right affect effect per gesture.
- body state refuses incoherent sleep/wake transitions and stays consistent with
  affect (no "sleeping with full energy").
- body state persists across a simulated restart when memory is enabled.
- `BehaviorProposal` accepts optional `intent`/`body_state`, validates/softens
  them, and strips them from the device frame; invalid `intent` is rejected.
- prompt includes body state, allowed animations, local time, presence duration,
  and relevant memories.
- touch while sleeping can wake; idle tick usually cannot wake before minimum nap.
- alert plus touch hold produces an acknowledgment/soothing path.
- transport: host parses multiple input frames from one socket read.
- transport: host ignores malformed uplink lines.
- transport: reader submits touch to `HostInputQueue`.
- transport: sender and reader survive a device disconnect and reconnect.
- transport: keepalive is emitted before the device timeout would elapse.

Firmware tests/smoke tests:

- touch GPIO reads stable idle and active states.
- gesture classifier unit tests (tap/hold/doubletap, boundaries, rate limit).
- tap/hold/doubletap classification works through the case.
- uplink frames are newline-delimited JSON and bounded.
- malformed host frames still do not block rendering.
- display continues animating while touch is sampled and Wi-Fi reconnects.
- local acknowledgement plays when the host link is down; nothing durable changes.
- long-sleep burn-in micro-motion keeps the panel from holding a static frame.

## Security / Local-network Posture

V2 adds more inputs, so make the (already reasonable) posture explicit:

- keep the HTTP control endpoints bound to `127.0.0.1` by default; `0.0.0.0` is
  opt-in for LAN clients.
- the AirPods helper posts only to localhost by default.
- the device TCP control socket accepts behavior frames from any LAN peer; treat
  this as trusted-network only.
- do not add authentication unless real LAN exposure becomes a requirement.

## Acceptance Criteria

- Mochi reacts immediately when the owner appears, leaves, touches, or sends an
  alert/message.
- Mochi can sleep for a believable stretch and only wakes for believable reasons.
- Drowsy/waking behavior exists as a real transition, not just a random animation.
- Present-but-ignored feels different from away.
- Touch can pet, soothe, wake, or invite play depending on body state and context.
- The LLM still makes meaningful choices, but every choice is grounded in host
  state, memory, time, and physical continuity.
- The device remains simple and bounded; rendering, Wi-Fi, and local idle survive
  disconnects and reconnects.
- Each milestone (V2a/V2b/V2c) passes its own gate before the next begins.
