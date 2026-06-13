# Hologotchi Plan

## Product

Hologotchi is a tiny holographic virtual pet for a desk: an ESP32 drives a 128x128 RGB OLED viewed through a dichroic cube, while a host service decides what the pet is feeling and doing.

The product is **one lovable pet**, not a general reactive platform. The goal is a pet with a clear personality that:

- feels alive even when nothing is happening
- responds to direct prompts in character
- reacts to a small number of useful computer signals
- can alert the user in-character when something important happens

The fastest path is to keep the device simple, keep the pet specific, and keep the control loop high-level.

## Product Decisions

- **Transport:** Wi-Fi only for runtime control. No USB control protocol in the product plan. USB may still be used for flashing and power.
- **MCU:** Keep **ESP32-C3** unless real implementation proves it too tight. The model stays off-device, so C3 should be enough for SSD1351 rendering plus Wi-Fi if the renderer stays simple.
- **Brain:** The host or local network service is the pet brain. The ESP only renders, animates, connects to Wi-Fi, and holds short-lived local state.
- **Host stack:** Python first.
- **Model runtime:** Ollama first.
- **Default model family:** `qwen3.5`.
- **Default preset:** `qwen3.5:4b`.
- **Low-memory fallback:** `qwen3.5:2b`.
- **Scope rule:** One pet, one transport, one control loop, and only the inputs that make Mochi feel more alive.

## Hardware

- MCU: ESP32-C3 for now.
- Display: Waveshare 1.5 inch RGB OLED, 128x128, SSD1351, SPI, RGB565.
- Optics: dichroic cube, so orientation and mirror correction are part of rendering correctness.
- Runtime transport: Wi-Fi on the local network.
- Power/flash path: USB is allowed for development, but not as the runtime control path.
- Memory note: one 128x128 RGB565 framebuffer is 32 KB, so the renderer should stay simple and predictable.

### ESP32-C3 vs ESP32-S3

For this simpler Wi-Fi-first product, **ESP32-C3 is still a valid target**. It does not need to become ESP32-S3 just because the control path moved to Wi-Fi.

Move to **ESP32-S3** only if one of these becomes true:

- Wi-Fi plus rendering leaves too little RAM headroom in practice
- the final art style needs larger buffers, more sprites, or more aggressive effects
- you want extra breathing room for future features and faster iteration

Until that happens, keeping C3 avoids a hardware pivot and keeps the project moving.

## Pet Direction

Choose one pet and commit to it.

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

Current host-side pet state:

- `mood`
- `energy`
- `attention`
- `affection`
- `playfulness`
- `sleepiness`
- `quiet_cycles`
- `last_event`

## Inputs

Keep inputs narrow and high-signal. The current supported inputs are:

- direct user messages to the pet
- build/test success or failure
- one generic important alert path

Add new inputs only when they clearly improve the pet. Do not build a large adapter system.

## Wi-Fi Control Path

Keep the wire contract simple.

Use **newline-delimited JSON over a single Wi-Fi connection** between the host and the device. The transport can be a plain local TCP connection on a trusted LAN. Do not add a bigger protocol unless working code proves it necessary.

Example behavior update:

```json
{"v":1,"kind":"behavior","mood":"sleepy","animation":"nap","text":"still here","alert":false,"duration_ms":8000}
```

Current behavior fields:

- `v`
- `kind`
- `mood`
- `animation`
- `text`
- `alert`
- `duration_ms`

Keep messages high-level. The current wire contract does **not** need:

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
- walk
- happy
- play
- excited
- sleepy
- nap
- worried
- alert

## Roadmap

1. **Lock the pet** — done; see [PET.md](PET.md).
   - Choose the shiba-like pet direction. → **Mochi**, a holographic shiba desk pet.
   - Write a short personality prompt. → playful/meme-forward system prompt in PET.md.
   - Define the core behaviors. → Current set: `idle`, `blink`, `look_around`, `walk`, `happy`, `play`, `excited`, `sleepy`, `nap`, `worried`, `alert`.

2. **Bring up the display** — firmware implemented in `device/`; pending on-hardware tuning.
   - SSD1351 init → `device/src/display.rs` (deterministic init sequence, blocking SPI).
   - orientation and mirror correction → single explicit `Orientation` re-map path; `Orientation::CUBE_ROTATED_CW` for the sideways dichroic-cube mount (flags tunable at bring-up).
   - one idle animation → `device/src/render.rs`: Mochi `idle` with integer breathing + occasional `blink`.

3. **Bring up Wi-Fi** — done; firmware has local TCP behavior transport.
   - join network
   - reconnect cleanly
   - prove the device can receive a behavior update over Wi-Fi

4. **Build the smallest host service** — done; Python host sends validated behavior updates.
   - one Python process
   - one LLM call path
   - one behavior message sent to the device

5. **Implement the pet loop** — done; host keeps short-lived state and can run continuously.
   - host keeps pet state → `host/state.py`
   - host asks the model what the pet does next → `host/pet_loop.py`
   - device animates the answer → persistent TCP behavior stream drives existing renderer

6. **Add direct interaction** — done; host can accept queued direct messages.
   - send the pet a message → `POST /message` on the host HTTP endpoint
   - get an in-character response and behavior → pet loop consumes the message as the next stateful event

7. **Add limited indirect inputs** — done; host HTTP endpoint accepts build/test results and one important alert path.
   - build/test result → `POST /build` and `POST /test` while the host loop is served
   - one important alert path → `POST /alert` while the host loop is served

8. **Polish** — done; prompt, renderer timing, and demo flow are ready for first footage.
   - tune prompt/personality → tighter Mochi prompt in [PET.md](PET.md) and event
     guidance in `host/state.py`
   - tune animation timing → local idle cadence and pose timing in
     `device/src/render.rs`
   - film the first demo → shot list and trigger commands in [DEMO.md](DEMO.md)

9. **Pet psychology, memory, and self-direction (host-only brain)** — done. No
   device or wire-protocol changes were required.

   Implemented (host-only): real-time needs/relationship decay and
   escalation in [`host/affect.py`](host/affect.py); the away / present-but-
   ignoring / engaged presence state machine in
   [`host/presence.py`](host/presence.py); and the versioned, thread-safe,
   salience-scored SQLite memory store with bounded ranked retrieval and
   inspect/forget/reset/pause controls in [`host/memory.py`](host/memory.py).
   The pet loop now injects a clock, decays affect with presence, applies
   owner-event effects, captures and recalls memory, and persists state across
   restarts. Presence signals (`POST /presence`) and memory controls
   (`GET /memory`, `POST /memory/forget|reset|writes`) are exposed on the
   existing control server. Self-direction and surprise (9d) are implemented in
   [`host/pet_loop.py`](host/pet_loop.py) with recent-animation novelty,
   spontaneous old-memory callbacks, rare earned moments, bounded
   self-initiated nudges, and adaptive loop cadence. Emotional-range/safety
   polish (9e) is implemented through prompt guidance, bounded deterministic
   affect recovery, existing-animation mapping, privacy controls, and guarded
   observed-pattern consolidation in [`host/reflection.py`](host/reflection.py).

   Goal: turn Mochi from a stateless reaction-picker into a persistent creature
   with needs, moods, memory, presence awareness, and self-direction, so it feels
   genuinely alive and surprises its owner — entirely on the host, reusing the
   existing one-way behavior protocol and the closed animation vocabulary.

   Keep everything here on the host. Do not move memory, planning, or
   interpretation onto the ESP32-C3. Use one local owner profile; no multi-user
   identity system. Memory is fully local and private by design.

   ### Architecture: deterministic continuity, LLM expression

   Split Mochi's brain into two cooperating layers:

   - **Continuity (deterministic, testable):** host state owns durable truth:
     needs, drives, affect, presence, relationship state, memory records, emotional
     bounds, recovery, and what actually happened. These are pure functions over
     state plus elapsed wall-clock time where possible. No model calls are required
     to know whether Mochi is lonely, rested, secure, frustrated, ignored, or
     recovering.
   - **Expression (LLM):** given the host's compact situation summary plus a few
     retrieved memories, the model picks exactly one in-character behavior and tiny
     phrase from the existing closed vocabulary. The model supplies interpretation,
     voice, variety, callbacks, and surprise inside the host-owned bounds.

   The LLM may influence continuity, but it must not silently own or rewrite it.
   Treat model-generated reflections as **candidates**, not facts. A proposed
   learned preference, emotional association, or owner fact only becomes durable
   memory after deterministic salience, repetition, conflict, and privacy checks.
   This lets Mochi develop an individual personality over time without fabricating
   history or drifting into random chatbot behavior.

   This makes needs, "ignored" logic, and emotional continuity deterministic and
   unit-testable while the LLM provides personality and surprise. The reactive stat
   nudges already in `host/state.py` are promoted from after-the-fact bookkeeping
   to the actual driver of each prompt.

   ### 9a — Needs, drives, and real-time decay (the tamagotchi core)

   A real tamagotchi has needs that decay in **wall-clock time**, are replenished by
   owner action, and have visible, bounded, recoverable consequences when neglected.

   - drives decay over real elapsed time, not loop ticks: `social`, `play`, `rest`,
     `stimulation`, plus the existing `energy`/`sleepiness`.
   - owner actions and good events replenish drives; neglect lets them fall.
   - escalating but recoverable states: content → restless → needy → sad/grumpy →
     withdrawn (a low-energy "sulk"), always recoverable through attention, play,
     praise, rest, or apology.
   - a slow **bond level** that grows over days of healthy interaction: the
     long-term arc that makes Mochi worth keeping on the desk.

   Decay and recovery are integer/fixed-point, driven by elapsed seconds since the
   last update, so host restarts and variable loop cadence stay correct.

   ### 9b — Presence and "being ignored" (the host computer is the sensor)

   "How does it know it's being ignored?" is a first-class requirement. The host
   computer is already a rich sensor; use it before adding any hardware. Replace the
   single `quiet_cycles` counter with a presence state machine that distinguishes:

   - **away** — no host activity and/or the screen is locked. Mochi waits calmly or
     naps; a genuine absence is not treated as rejection.
   - **present-but-ignoring** — keyboard/mouse active or a foreground app in use,
     but no direct interaction with Mochi for a while. *This* is real "ignored," and
     it grows loneliness/neediness over real time.
   - **engaged** — a recent direct message, boop, or acknowledged alert.

   Signals, cheapest first, all host-side and opt-in:

   - OS idle time since last input, and screen-lock state
   - foreground app / whether the owner is heads-down vs idle
   - time-of-day and a learned daily routine ("usually here by 9; it's 9:40")
   - optional later: webcam face-presence, or calendar busy/free

   With presence, Mochi behaves like a pet: excited when you return after a real
   absence, pouty if you've been at the desk ignoring it, sleepy when the screen and
   room go quiet at night. (Note: **jealousy** needs a target — e.g. long focus on
   one app — so it belongs here once a foreground-app signal exists, not as a random
   mood.)

   ### 9c — Memory

   Keep memory local, structured, and bounded. Start with SQLite for persistence;
   keep the first retrieval simple and testable with tags, recency, importance, and
   SQLite full-text search. Embeddings via Ollama can come later; vector search must
   not be required for the first useful memory system.

   Memory types:

   - **short-term:** recent messages, events, behavior choices, phrases, unresolved
     emotional threads, the current interaction arc.
   - **episodic:** specific meaningful moments — praise, ignored alerts, repeated
     build failures, passing tests, apologies, long absences, affection.
   - **semantic:** stable learned facts — owner preferences, recurring projects,
     phrases the owner uses, things Mochi likes, things that reliably move its mood.
   - **affect:** learned emotional associations that change future reactions —
     failed builds making Mochi protective, praise making it proud, being ignored
     making it lonely.
   - **relationship state:** attachment, trust, affection, loneliness, frustration,
     play drive, security, forgiveness, confidence — plus the 9a bond level.

   Durable memory records stay small and structured: timestamp, source, short
   summary, tags/entities, emotional valence, emotional intensity, importance,
   decay/retention policy, last-recalled time, recall count.

   **Salience rubric** (decides what to store; do not store every idle tick): score
   on valence magnitude, novelty, whether the owner initiated it, alert status, and
   repetition. Store only above a threshold; let importance decay over time and tick
   up on recall.

   Host-loop lifecycle each tick:

   1. Capture the current input or the body's self-directed situation.
   2. Score salience; store meaningful moments only.
   3. Update short-term state and unresolved emotional threads.
   4. Retrieve a bounded, ranked set of relevant memories and learned facts.
   5. Build a compact prompt: current pet/needs/relationship state, a few retrieved
      memories, learned preferences, and the current situation.
   6. Ask the model for one validated behavior using the existing schema.
   7. Apply deterministic affect/drive updates.
   8. Persist any new important memory.
   9. Periodically consolidate repeated events into stable facts (see 9e).

   Keep the prompt compact — prefer a few emotionally relevant memories over a long
   history dump. Example shape:

   ```text
   Relevant memories:
   - Yesterday, owner praised Mochi after tests passed. Mochi felt proud.
   - Earlier today, two builds failed in a row. Mochi became worried.
   - Owner often says "good pup" when pleased. Mochi loves that.

   Relationship:
   - affection: 78/100
   - trust: 72/100
   - loneliness: 34/100
   - frustration: 18/100
   - current thread: Mochi wants attention after a quiet stretch.
   ```

   ### 9d — Surprise and self-direction — done

   "Surprise the owner often" needs explicit mechanisms; a small (4B) model left to
   its own devices repeats itself. Add:

   - **novelty pressure:** avoid repeating the recent animation *and* phrase (extend
     the existing `recent_phrases` guard to animations; weight retrieval toward
     fresh material).
   - **spontaneous callbacks:** occasionally inject one relevant old memory so Mochi
     "brings something up" days later.
   - **rare special moments:** milestones (100th green build), streaks, first
     interaction of the day, day/night beats — low-probability, earned-feeling
     events.
   - **self-initiated nudges:** when a drive crosses a threshold, Mochi may start a
     game, patrol, or — rarely and bounded — ask for attention on its own, including
     a gentle self-made alert ("heads-down 2h?").
   - **adaptive cadence:** slow the loop right down when napping or away, speed it up
     on events. This feels alive and avoids pinning the model on the GPU 24/7.

   ### 9e — Emotional range, privacy, controls, and safety rails — done

   Emotional range (normal operating range medium; strong negative as a bounded
   upper edge, like a real pet having a hard moment):

   - ignored too long → needy, sad, then briefly grumpy or avoidant
   - repeated failures → worried, protective, then stressed
   - affection/praise → happy, bonded, playful, proud
   - harsh owner messages → hurt, sad, defensive, briefly upset
   - owner returns after absence → excited if secure; pouty/dramatic if lonely
   - important alert dismissed → anxious or insistent for a bounded period
   - apologies, affection, play, time, and good outcomes repair negative states;
     nothing becomes permanent, and Mochi is never manipulative.

   Renderer compatibility (ship psychology with no firmware change by mapping new
   feelings onto the existing 11 animations):

   - sad → `worried`, `sleepy`, or `idle`
   - angry/grumpy → `worried`, `look_around`, `walk`, or `alert` by intensity
   - needy → `play`, `look_around`, or `happy`
   - pouty → `idle`, `sleepy`, or `worried`, with rare short text
   - only add explicit `sad`/`pout`/`grumpy`/`love`/`confused` animations later, if
     the memory system proves they are needed.

   Required controls (memory is not complete until these work):

   - inspect a local memory summary
   - forget one memory
   - forget by tag/source/time range
   - reset Mochi's memory and relationship state
   - disable memory writes for a session

   Persistence and safety rails:

   - version the SQLite schema and ship forward migrations; forget/reset operate on
     the versioned store.
   - guard concurrency: HTTP inputs arrive on `ThreadingHTTPServer` threads while the
     loop reads/writes state and memory — serialize memory/state access (single
     writer or a lock) so inputs and the loop never corrupt each other.
   - **consolidation guardrail:** derive "facts" from observed counters/patterns, not
     free model invention; if the model proposes a fact, require repetition/threshold
     before it becomes durable, so Mochi never fabricates beliefs about the owner.
   - any activity/presence sensing (9b) is explicit opt-in and stays local.

   ### Suggested host modules

   - `host/affect.py`: needs, drives, decay, mood, attachment, trust, loneliness,
     frustration, forgiveness, recovery, bond level — pure and deterministic.
   - `host/presence.py`: the away / present-but-ignoring / engaged state machine and
     its host activity signals.
   - `host/memory.py`: SQLite store, capture, salience scoring, bounded ranked
     retrieval, durable records, forget/reset.
   - `host/reflection.py`: periodic consolidation of repeated events into stable
     facts and preferences, behind the guardrail above.
   - `host/profile.py`: single-owner local profile, Mochi and learned-owner
     preferences, forget/reset operations.

   ### Testing

   All body logic — needs decay, presence transitions, affect, salience, retrieval
   ranking, consolidation, prompt construction, forget/reset — is deterministic and
   tested with a **stubbed model and an injected clock**. The loop already injects
   `generate` and `sleep`; add an injectable time source so wall-clock decay and
   "ignored" timing are testable without real waiting.

   ### Acceptance for Phase 9

   - Mochi's needs decay in real time and recover through owner action.
   - Mochi distinguishes *away* from *present-but-ignoring* and reacts differently.
   - Mochi remembers meaningful interactions across host restarts.
   - each model call gets only a bounded, relevant memory context.
   - repeated owner behavior changes relationship state in a testable way.
   - Mochi can become sad, needy, worried, grumpy, or (grounded) jealous for
     understandable reasons, always bounded, recoverable, and never manipulative.
   - Mochi surprises the owner: novelty, spontaneous callbacks, and rare special
     moments are observable over a session.
   - rare remembered text references work without turning Mochi into a chatbot.
   - memory is fully local with working inspect/forget/reset/disable controls, a
     versioned schema, and thread-safe access.
   - the device firmware and wire protocol are unchanged for this phase.

## Achieved Baseline

The initial useful product baseline is in place when:

- the device boots into a recognizable idle pet without the host
- the device joins Wi-Fi and reconnects after a drop
- the host can send a behavior update that changes what the pet is doing
- the pet responds to a direct prompt in character
- the pet reacts to build/test changes and one alert path
- a short demo makes it feel like a real desk pet, not a generic animated display

## Deferred

- USB runtime control
- a large event framework
- a reflex/personality split
- a scheduler or priority system
- a scene protocol or graphics abstraction layer
- many input adapters
- on-device audio
- on-device model inference
- broad future architecture design before the pet is already fun
