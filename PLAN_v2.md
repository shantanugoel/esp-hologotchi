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
- Keep the behavior animation vocabulary closed unless the renderer really needs
  new art.
- Do not build a generic adapter/event framework.
- Do not move to ESP32-S3 for touch/presence alone. ESP32-C3 remains fine.
- Firmware remains no_std-friendly: fixed buffers, bounded JSON, deterministic
  debouncing, rate-limited input frames.

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

Durable truth the host updates deterministically:

- presence: `away`, `present_but_ignoring`, `engaged`
- body state: `awake`, `drowsy`, `sleeping`, `waking`
- elapsed durations: time away, time present, time ignored, time asleep
- affect/drives: social, play, rest, stimulation, energy, sleepiness
- relationship: affection, trust, loneliness, frustration, bond
- recent events and memories

This state provides inertia. It prevents deep sleep -> zoomies -> sad -> nap
within a few idle ticks unless real events justify it.

### Layer 3: LLM Pet Mind

The LLM receives a compact state summary and chooses Mochi's next action. It may
propose:

- mood
- animation
- short text
- intent
- next body state
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

Add one uplink message family on the same single TCP connection:

```json
{"v":1,"kind":"input","source":"touch","gesture":"tap","ms":1234}
{"v":1,"kind":"input","source":"touch","gesture":"hold","duration_ms":1200,"ms":2345}
{"v":1,"kind":"input","source":"touch","gesture":"doubletap","ms":3456}
```

Rules:

- `behavior` frames are host -> device.
- `input` frames are device -> host.
- Both are newline-delimited JSON.
- Firmware writes only small bounded input frames.
- Malformed uplink frames are ignored by the host.
- Malformed downlink frames are ignored by firmware.
- Input frames must never block display rendering.

## Presence

### General Presence Signal

Presence should be source-agnostic. AirPods are the first source, but the host
loop should not know AirPods-specific details.

Preferred `/presence` payload:

```json
{
  "present": true,
  "source": "airpods",
  "confidence": 0.9,
  "ttl_seconds": 30
}
```

Disconnected:

```json
{
  "present": false,
  "source": "airpods",
  "confidence": 0.8,
  "ttl_seconds": 30
}
```

Also keep compatibility with the existing host-activity fields:

```json
{
  "idle_seconds": 12,
  "screen_locked": false,
  "foreground_app": "Code"
}
```

Presence classification:

- recent direct message or touch -> `engaged`
- `present=false` -> `away`
- `present=true` and no recent interaction -> `present_but_ignoring`
- screen locked or host idle past threshold -> `away`
- screen unlocked or active idle reading -> `present_but_ignoring`
- missing/expired signals -> unknown, then use existing fallback logic

On meaningful state changes, `/presence` must enqueue a loop event so Mochi
reacts immediately:

```text
Presence changed: owner returned after 37 minutes away.
Presence changed: owner left.
Presence changed: owner is nearby but has not interacted for 15 minutes.
```

### AirPods Helper

Add a separate optional host helper:

```bash
python -m host.airpods_presence --name "Shantanu's AirPods" --url http://localhost:8765/presence
```

Responsibilities:

- poll Bluetooth state every 5-10 seconds
- post `present=true` when the named AirPods are connected
- post `present=false` when disconnected
- include `ttl_seconds`
- debounce changes; require 2 consecutive readings before flipping
- log only connection state and POST errors

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

Avoid the display pins. `GPIO5` is the preferred first touch input. `GPIO10` is a
reasonable backup if board routing is cleaner. Power the TTP223 from 3.3V so the
OUT signal is safe for the ESP32-C3.

For enclosure placement:

- mount the touch pad behind the case where a user naturally taps/pets the cube
- 2mm PLA is likely workable with a reasonably large pad
- use copper foil around 20-30mm square if the module's onboard pad is unreliable
- keep the pad away from metal, USB shields, display flex, and long SPI runs
- prototype sensitivity before committing the case

### Firmware Gesture Classifier

Start with three gestures:

- `tap`: press shorter than 500ms
- `hold`: press at least 700ms
- `doubletap`: two taps within 350-450ms

Firmware requirements:

- debounce input
- rate-limit events
- use a tiny fixed-size event queue
- classify gestures deterministically
- keep rendering independent from touch sampling
- send only compact JSON input frames

Example uplink frames:

```json
{"v":1,"kind":"input","source":"touch","gesture":"tap","ms":1234}
{"v":1,"kind":"input","source":"touch","gesture":"hold","duration_ms":960,"ms":2234}
{"v":1,"kind":"input","source":"touch","gesture":"doubletap","ms":3001}
```

### Host Touch Meaning

Touch is real engagement. Add touch to the engagement sources.

Suggested effects:

- `tap`: boop, attention, light wake-up
- `hold`: petting, soothing, affection repair, alert acknowledgment
- `doubletap`: play invite

Host-side event text examples:

```text
Touch input: boop tap.
Touch input: gentle pet hold for 1200ms.
Touch input: double boop play invite.
```

Touch should wake the loop immediately.

## Body State

Add a small body state model to `host/state.py` or a new `host/body.py`.

Suggested state:

```python
class BodyState(str, Enum):
    AWAKE = "awake"
    DROWSY = "drowsy"
    SLEEPING = "sleeping"
    WAKING = "waking"
```

Track:

```text
body_state
body_state_since
sleep_started_at
last_touch_at
last_presence_change_at
last_owner_seen_at
last_play_at
last_soothed_at
```

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

Keep the device behavior schema unchanged, but let the host ask the model for
extra host-only fields. The host strips them before sending to the device.

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
- If proposed transition is invalid, soften it to the nearest valid state and
  behavior.

Do not send `intent` or `body_state` to the ESP32-C3 in V2 unless firmware later
needs it. The device only needs the existing behavior frame.

## Prompt Contract

The prompt should tell the LLM what is physically true, what it may choose, and
what would be incoherent.

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
second scheduler.

## Demo Script

Implement V2 so these moments work reliably:

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

1. Extend host presence payloads with `present`, `source`, `confidence`, and
   `ttl_seconds`.
2. Make meaningful presence transitions enqueue immediate loop events.
3. Add the AirPods presence helper.
4. Add host-side touch input first, e.g. `POST /touch`, so behavior can be tested
   before firmware uplink exists.
5. Add the body state model and transition validation.
6. Tighten prompt construction with body state, allowed transitions, allowed
   animations, local time, and relevant memories.
7. Let model output optional host-only `intent` and `body_state`; strip them before
   sending behavior to the device.
8. Add TTP223 firmware input sampling and gesture classification.
9. Add device -> host uplink frames on the existing TCP connection.
10. Add a host transport reader that parses uplink input frames and submits them
    to `HostInputQueue`.
11. Tune thresholds against the demo script.

## Suggested Host Modules

- `host/presence.py`: expanded source-agnostic presence signals, TTL, state-change
  reports.
- `host/airpods_presence.py`: optional platform helper that posts `/presence`.
- `host/inputs.py`: touch input submission and validation.
- `host/body.py`: body state, transition rules, allowed animation sets.
- `host/state.py`: prompt context and recent behavior tracking.
- `host/pet_loop.py`: immediate events, body transition application, prompt flow.
- `host/transport.py`: bidirectional socket client for behavior downlink and input
  uplink.

## Suggested Firmware Changes

- Add TTP223 input pin config, initially `GPIO5`.
- Add debounced touch sampler.
- Add deterministic tap/hold/doubletap classifier.
- Add small bounded queue for input events.
- Update control socket task to write queued input frames while still reading
  behavior frames.
- Keep local idle rendering alive if the host disconnects.

## Tests

Host tests:

- `/presence` accepts present/source/confidence/ttl payloads.
- expired presence falls back safely.
- AirPods helper debounces before posting changed state.
- presence state changes enqueue immediate events.
- `/touch` validates gestures and enqueues touch events.
- touch counts as engagement.
- body state refuses incoherent sleep/wake transitions.
- model-proposed `body_state` is accepted, softened, or rejected correctly.
- prompt includes body state, allowed animations, local time, presence duration,
  and relevant memories.
- touch while sleeping can wake; idle tick usually cannot wake before minimum nap.
- alert plus touch hold produces an acknowledgment/soothing path.

Firmware tests/smoke tests:

- touch GPIO reads stable idle and active states.
- tap/hold/doubletap classification works through the case.
- uplink frames are newline-delimited JSON and bounded.
- malformed host frames still do not block rendering.
- display continues animating while touch is sampled and Wi-Fi reconnects.

## Acceptance Criteria

- Mochi reacts immediately when the owner appears, leaves, touches, or sends an
  alert/message.
- Mochi can sleep for a believable stretch and only wakes for believable reasons.
- Drowsy/waking behavior exists as a real transition, not just a random animation.
- Present-but-ignored feels different from away.
- Touch can pet, soothe, wake, or invite play depending on body state and context.
- The LLM still makes meaningful choices, but every choice is grounded in host
  state, memory, time, and physical continuity.
- The device remains simple and bounded.
