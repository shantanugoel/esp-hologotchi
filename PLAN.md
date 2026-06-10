# Hologotchi Plan

## Product

Hologotchi is a tiny holographic virtual pet for a desk: an ESP32 drives a 128x128 RGB OLED viewed through a dichroic cube, while a host service decides what the pet is feeling and doing.

V1 should be **one lovable pet**, not a general reactive platform. The goal is a pet with a clear personality that:

- feels alive even when nothing is happening
- responds to direct prompts in character
- reacts to a small number of useful computer signals
- can alert the user in-character when something important happens

The fastest path is to keep the device simple, keep the pet specific, and keep the control loop high-level.

## V1 Decisions

- **Transport:** Wi-Fi only for runtime control. No USB control protocol in the product plan. USB may still be used for flashing and power.
- **MCU:** Keep **ESP32-C3** for V1 unless real implementation proves it too tight. The model stays off-device, so C3 should be enough for SSD1351 rendering plus Wi-Fi if the renderer stays simple.
- **Brain:** The host or local network service is the pet brain. The ESP only renders, animates, connects to Wi-Fi, and holds short-lived local state.
- **Host stack:** Python first.
- **Model runtime:** Ollama first.
- **Default model family:** `qwen3.5`.
- **Default preset:** `qwen3.5:4b`.
- **Low-memory fallback:** `qwen3.5:2b`.
- **Scope rule:** One pet, one transport, one control loop, a few inputs.

## Hardware

- MCU: ESP32-C3 for V1.
- Display: Waveshare 1.5 inch RGB OLED, 128x128, SSD1351, SPI, RGB565.
- Optics: dichroic cube, so orientation and mirror correction are part of rendering correctness.
- Runtime transport: Wi-Fi on the local network.
- Power/flash path: USB is allowed for development, but not as the runtime control path.
- Memory note: one 128x128 RGB565 framebuffer is 32 KB, so the renderer should stay simple and predictable.

### ESP32-C3 vs ESP32-S3

For this simpler Wi-Fi-first product, **ESP32-C3 is still a valid V1 target**. It does not need to become ESP32-S3 just because the control path moved to Wi-Fi.

Move to **ESP32-S3** only if one of these becomes true:

- Wi-Fi plus rendering leaves too little RAM headroom in practice
- the final art style needs larger buffers, more sprites, or more aggressive effects
- you want extra breathing room for future features and faster iteration

Until that happens, keeping C3 avoids a hardware pivot and keeps the project moving.

## Pet Direction

V1 should choose one pet and commit to it.

Recommended default: **a shiba-like desk pet** with internet-pet energy. Think “small holographic shiba companion,” not a generic blob. It should read instantly through the cube, have a strong silhouette, and be easy to animate with a tiny asset set.

Why this is the fastest option:

- recognizable and emotionally legible
- easier to build attachment around than an abstract creature
- simpler to market than a generic “AI sprite”
- still flexible enough to borrow some playful, meme-adjacent energy from things like Nyan Cat

Personality baseline:

- affectionate
- curious
- slightly needy
- dramatic about wins and failures
- calm when idle
- capable of alerting the user without feeling like a sterile notification system

## Architecture

### Device

The device should:

- connect to Wi-Fi
- render the pet continuously on the OLED
- keep a local idle loop running when the host is unavailable
- accept simple high-level behavior updates over Wi-Fi
- avoid model inference, long-term planning, and complex interpretation

The device should only hold short-lived state such as:

- current mood
- current animation
- text bubble
- alert flag
- timers
- connection status

### Host

The host should:

- maintain the pet's longer-lived state
- gather a small number of inputs
- call the LLM periodically and on meaningful events
- validate model output
- send the next behavior to the device over Wi-Fi

The host owns the actual pet loop. The device only performs it.

## Pet Loop

The pet should feel LLM-controlled all the time, but the model should not be asked to drive every frame.

Instead:

1. The host keeps a small persistent pet state.
2. Every few seconds, or when something meaningful happens, the host asks the model what the pet should do next.
3. The host validates the answer.
4. The host sends a single high-level behavior update to the device.
5. The device animates smoothly until the next update arrives.

This keeps the product simple while still making the LLM feel like the brain.

Recommended host-side pet state for V1:

- `mood`
- `energy`
- `attention`
- `affection`
- `sleepiness`
- `last_event`

## Inputs for V1

Keep V1 narrow. Only support:

- direct user messages to the pet
- build/test success or failure
- one generic important alert path

Everything else is later. Do not build a large adapter system yet.

## Wi-Fi Control Path

Keep the wire contract simple.

Use **newline-delimited JSON over a single Wi-Fi connection** between the host and the device. The transport can be a plain local TCP connection on a trusted LAN. Do not add a bigger protocol unless working code proves it necessary.

Example behavior update:

```json
{"v":1,"kind":"behavior","mood":"sleepy","animation":"curl_up_then_peek","text":"still here...","alert":false,"duration_ms":8000}
```

Recommended V1 fields:

- `v`
- `kind`
- `mood`
- `animation`
- `text`
- `alert`
- `duration_ms`

Keep messages high-level. V1 does **not** need:

- a generic event schema
- a reflex engine
- a scheduler
- a scene graph
- a large command taxonomy

## Rendering

Rendering should optimize for speed and readability, not feature depth.

- strong silhouette through the cube
- big emotional changes in eyes, posture, and motion
- tiny asset set
- minimal text
- integer-friendly animation
- one corrected orientation path, used consistently everywhere

The first useful animation set is enough:

- idle
- blink
- look around
- happy
- sleepy
- worried
- alert

## Roadmap

1. **Lock the pet** — done; see [PET.md](PET.md).
   - Choose the shiba-like pet direction. → **Mochi**, a holographic shiba desk pet.
   - Write a short personality prompt. → playful/meme-forward system prompt in PET.md.
   - Define the first 5 to 7 core behaviors. → 7 locked: `idle`, `blink`, `look_around`, `happy`, `sleepy`, `worried`, `alert`.

2. **Bring up the display**
   - SSD1351 init
   - orientation and mirror correction
   - one idle animation

3. **Bring up Wi-Fi**
   - join network
   - reconnect cleanly
   - prove the device can receive a behavior update over Wi-Fi

4. **Build the smallest host service**
   - one Python process
   - one LLM call path
   - one behavior message sent to the device

5. **Implement the pet loop**
   - host keeps pet state
   - host asks the model what the pet does next
   - device animates the answer

6. **Add direct interaction**
   - send the pet a message
   - get an in-character response and behavior

7. **Add limited indirect inputs**
   - build/test result
   - one important alert path

8. **Polish**
   - tune prompt/personality
   - tune animation timing
   - film the first demo

## Acceptance for V1

V1 is successful when:

- the device boots into a recognizable idle pet without the host
- the device joins Wi-Fi and reconnects after a drop
- the host can send a behavior update that changes what the pet is doing
- the pet responds to a direct prompt in character
- the pet reacts to build/test changes and one alert path
- a short demo makes it feel like a real desk pet, not a generic animated display

## Explicitly Out of Scope for V1

- USB runtime control
- a large event framework
- a reflex/personality split
- a scheduler or priority system
- a scene protocol or graphics abstraction layer
- many input adapters
- on-device audio
- on-device model inference
- broad V2 architecture design before the pet is already fun
