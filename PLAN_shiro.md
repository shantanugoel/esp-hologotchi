# Shiro Migration Plan

Goal: move the private desk pet from geometric Mochi/Shiba rendering to a
faithful hand-drawn Shiro-style dog while keeping the V1 system simple:
host-owned intelligence, Wi-Fi behavior updates, firmware-local idle behavior,
and no generic scene graph.

This is a private personal build. Treat the Shiro likeness as acceptable for
this repo, but keep implementation choices practical for the ESP32-C3.

## Current Assets

- `assets/sprites/shiro_sheet_4x4_128.png`
  - 512x512 RGBA sheet.
  - 4 columns x 4 rows.
  - 128x128 per pose.
  - Transparent background.
- `assets/sprites/shiro_sheet.png`
  - Larger transparent generated source.
- `assets/sprites/shiro_sheet_chromakey.png`
  - Original green-screen generation source.
- `assets/sprites/shiro_sheet.json`
  - Pose metadata and pose order.

Current pose order:

1. `sitting_idle`
2. `blink`
3. `happy_bounce`
4. `tail_wag_left`
5. `tail_wag_right`
6. `sleeping_curled`
7. `alert_ears_up`
8. `confused_head_tilt`
9. `sad_droop`
10. `excited_jump`
11. `listening`
12. `looking_left`
13. `looking_right`
14. `paw_lift`
15. `surprised`
16. `relaxed_loaf`

## Principles

- Do not procedurally redraw Shiro with circles and ellipses. That is what made
  the current pet feel too geometric.
- Use hand-drawn sprite poses as the primary visual source.
- Keep the ESP32-C3 renderer deterministic and `no_std` friendly.
- Keep model inference, planning, context interpretation, and persistent pet
  state on the host.
- Preserve the V1 wire protocol unless a real implementation problem proves a
  change is necessary.
- Keep the behavior vocabulary small and closed.
- Validate the look on the real SSD1351 through the dichroic cube before
  expanding the full animation set.

## Phase 1: Lock The Shiro Spec

Update `PET.md` so the single source of truth describes Shiro instead of
Mochi/Shiba.

Keep the existing behavior vocabulary:

- `idle`
- `blink`
- `look_around`
- `confused`
- `walk`
- `happy`
- `play`
- `excited`
- `sleepy`
- `nap`
- `worried`
- `alert`

Keep the existing mood vocabulary:

- `calm`
- `curious`
- `happy`
- `sleepy`
- `worried`
- `alert`

Expected edits:

- Rename the pet identity from Mochi to Shiro.
- Rewrite the personality prompt around Shiro-like body language: soft floppy
  ears, tiny dot eyes, head tilts, curled tail, red collar, gentle bounces,
  sleepy loafing, and alert perk-ups.
- Remove Shiba/Doge-specific language where it no longer fits.
- Keep "not an assistant, not a chatbot" unchanged.
- Keep optional text short and ASCII.

Host protocol and device parser should not need changes in this phase.

## Phase 2: Normalize The Sprite Source

Use ImageMagick and/or FFmpeg on the host side only to clean the source sheet.

Tasks:

- Confirm every cell is exactly 128x128 in the normalized sheet.
- Ensure transparent pixels are really alpha, not green RGB that happens to be
  transparent.
- Remove any residual green spill around motion marks and outlines.
- Keep the black outline thick enough to survive RGB565 conversion.
- Keep the white fill bright enough to read over black on the OLED.
- Keep the red collar distinct but not over-saturated.
- Save cleaned output non-destructively if the art changes materially.

Good candidate commands:

```sh
magick assets/sprites/shiro_sheet_4x4_128.png -alpha on info:
magick assets/sprites/shiro_sheet_4x4_128.png -background black -alpha remove /tmp/shiro_black_check.png
magick assets/sprites/shiro_sheet_4x4_128.png -background magenta -alpha remove /tmp/shiro_alpha_check.png
```

Do not require ImageMagick on the device or at runtime.

## Phase 3: Build A Firmware Asset Converter

Add a host-side conversion script. The device should not decode PNGs.

Suggested location:

- `tools/convert_sprite_sheet.py`, or
- `host/tools/convert_sprite_sheet.py` if we want it under the Python package.

Inputs:

- `assets/sprites/shiro_sheet_4x4_128.png`
- `assets/sprites/shiro_sheet.json`

Output:

- `device/src/shiro_sprites.rs`

The converter should:

- Read RGBA PNG cells.
- Quantize colors into a tiny fixed palette.
- Convert visible pixels to RGB565.
- Preserve transparency as a skip/run marker.
- Emit fixed Rust arrays.
- Emit pose IDs matching the metadata.
- Keep output deterministic so diffs are meaningful.

Recommended first format:

- One RLE stream per sprite.
- Each run stores:
  - transparent run length, or
  - solid RGB565 run length plus color.
- Keep run lengths bounded to `u8` or `u16`.
- Decode directly into the existing framebuffer through the display draw target.

Avoid:

- Runtime PNG decoding.
- Heap allocation.
- Floating-point transforms.
- Large per-frame temporary buffers.
- A generic scene graph.

## Phase 4: Replace The Device Renderer Internals

Keep the public renderer shape in `device/src/render.rs`:

- `Scene`
- `Scene::new`
- `Scene::apply_behavior`
- `Scene::tick`
- `Scene::draw`
- local idle behavior when no host update is active

Replace the Mochi geometric drawing path with a sprite blitter.

Renderer tasks:

- Add a sprite blit function for transparent RLE sprites.
- Map each `Animation` to one or more sprite pose IDs.
- Add deterministic frame timing for loops.
- Keep the alert border and optional text bubble unless they visually conflict
  with Shiro.
- Keep OLED orientation and cube mirror correction in the display layer.

Initial behavior-to-pose mapping:

| Animation | Initial pose |
| --- | --- |
| `idle` | `sitting_idle` |
| `blink` | `blink` |
| `look_around` | `looking_left`, `looking_right`, `listening` |
| `confused` | `confused_head_tilt` |
| `walk` | `looking_left`, `looking_right` as temporary side poses |
| `happy` | `tail_wag_left`, `tail_wag_right` |
| `play` | `paw_lift` |
| `excited` | `excited_jump`, `happy_bounce` |
| `sleepy` | `relaxed_loaf` |
| `nap` | `sleeping_curled` |
| `worried` | `sad_droop` |
| `alert` | `alert_ears_up`, `surprised` |

Small procedural motion is acceptable:

- 1-3 px vertical bob.
- Tiny x offsets for tail wag loops.
- Blink frame timing.
- Alert border pulse.

Do not rotate or scale sprites on device in V1.

## Phase 5: Complete Pose And Animation Coverage

The current 16-pose sheet is enough for a first renderer migration, but it is
not enough for polished animation. Treat it as the MVP pose source and then add
frames in small art passes.

Do not try to make every behavior unique immediately. First make the pet look
alive and faithful in the common states, then fill in rarer emotional and motion
states.

### Pose Naming

Use stable pose IDs so firmware tables and metadata stay readable:

- `idle_00`, `idle_01`, `idle_02`
- `blink_00`, `blink_01`, `blink_02`
- `look_center`, `look_left`, `look_right`, `listen_00`
- `happy_wag_00`, `happy_wag_01`, `happy_wag_02`
- `excited_00`, `excited_01`, `excited_02`
- `play_00`, `play_01`, `play_02`
- `sleepy_00`, `sleepy_01`
- `nap_00`, `nap_01`
- `worried_00`, `worried_01`
- `confused_00`, `confused_01`
- `alert_00`, `alert_01`
- `walk_left_00`, `walk_left_01`, `walk_right_00`, `walk_right_01`

Keep the old current-pose names in `shiro_sheet.json` until the first renderer
swap works. Rename or supersede them only when the converter supports the new
metadata cleanly.

### Sheet Organization

Keep each source sheet simple: 4x4 cells, 128x128 per cell, transparent
background.

Suggested future sheets:

- `shiro_core_4x4_128.png`: idle, blink, look, listen.
- `shiro_happy_4x4_128.png`: happy, excited, play, paw lift.
- `shiro_sleep_alert_4x4_128.png`: sleepy, nap, worried, alert.
- `shiro_walk_4x4_128.png`: optional side-profile patrol cycle.

The converter should accept multiple sheet entries in metadata before we exceed
the current 16-pose sheet. Do not pack unrelated resolutions or cell sizes into
the same asset pipeline.

### Behavior Frame Targets

| Animation | MVP frames | Polished target | Current coverage | Remaining art needed |
| --- | ---: | ---: | --- | --- |
| `idle` | 1 | 3 | `sitting_idle` | Two subtle breathing variants with same silhouette and eye direction. |
| `blink` | 1 | 3 | `blink` | Open/closed/open loop using the exact idle pose alignment. |
| `look_around` | 2 | 4 | `looking_left`, `looking_right`, `listening` | Center look plus smoother left/right scan frames. |
| `confused` | 1 | 2 | `confused_head_tilt` | Alternate head tilt or wobble frame; optional tiny question-like expression without text. |
| `walk` | 2 placeholder | 4-6 | `looking_left`, `looking_right` placeholder only | True side-profile walk cycle with paws shifting and tail curl stable. |
| `happy` | 2 | 3-4 | `tail_wag_left`, `tail_wag_right` | Stable body frame between tail extremes; optional eyes-happy frame. |
| `play` | 1 | 3 | `paw_lift` | Real play bow or invite pose; current paw lift is acceptable as temporary. |
| `excited` | 2 | 3-4 | `excited_jump`, `happy_bounce` | Bounce anticipation/landing frames aligned to avoid jitter. |
| `sleepy` | 1 | 2-3 | `relaxed_loaf` | Drowsy sitting/loaf frame with drooped ears and half eyes. |
| `nap` | 1 | 2 | `sleeping_curled` | Breathing variant, same curled silhouette. |
| `worried` | 1 | 2 | `sad_droop` | Smaller/shrunken pose or eyes-down variant. |
| `alert` | 2 | 3 | `alert_ears_up`, `surprised` | Braced alert pose and optional open-mouth/perked transition. |

### Animation Timing

Keep timing tables in firmware, not in the model output.

Suggested first timings at 20 fps:

- `idle`: 40-60 frames per art frame, slow loop.
- `blink`: closed frame for 2-3 frames.
- `look_around`: hold each direction for 12-20 frames.
- `happy`: tail alternates every 5-8 frames.
- `excited`: bounce alternates every 4-6 frames.
- `play`: paw/play-bow loop every 10-16 frames.
- `sleepy`: very slow blink or droop every 40-80 frames.
- `nap`: breathing frame every 50-90 frames.
- `worried`: slow wobble every 20-40 frames.
- `alert`: faster 6-10 frame pulse plus existing alert border.

Use integer frame counters and table lookups. Do not interpolate, rotate, or
scale sprites on device in V1.

### Art Pass Priority

Work in this order:

1. Core readability pass:
   `idle_00`, `blink_01`, `happy_wag_00`, `happy_wag_01`, `nap_00`,
   `alert_00`.
2. Local aliveness pass:
   `idle_01`, `idle_02`, `blink_00`, `blink_02`, `look_center`,
   `look_left`, `look_right`.
3. Emotional pass:
   `worried_00`, `worried_01`, `confused_00`, `confused_01`,
   `sleepy_00`, `sleepy_01`.
4. High-energy pass:
   `excited_00`, `excited_01`, `excited_02`, `play_00`, `play_01`,
   `play_02`.
5. Optional patrol pass:
   `walk_left_00`, `walk_left_01`, `walk_right_00`, `walk_right_01`.

Walking is intentionally last. It needs the most custom art to avoid looking
like a sliding static sprite. If it takes too long, keep `walk` as a curious
look-around or side-facing patrol pose for V1.

### Transition Policy

Do not create transition animations for every pair of behaviors. That would
explode the art workload.

Acceptable V1 transitions:

- Change directly from current loop to the first frame of the next behavior.
- Use a 1-3 px vertical bob on entry for happy/excited/play.
- Use a one-frame blink as a soft transition from idle to look/confused.
- Use the alert border pulse to carry urgency instead of extra alert art.

Only add explicit transition frames if a hardware smoke test shows a specific
switch looks bad.

### Frame Budget Gate

Raw 128x128 RGB565 frames are too expensive if stored directly. After each art
pass, run the converter and record the generated Rust size.

If the sprite data grows too large:

- Prefer cropped sprite bounds plus x/y offsets before dropping poses.
- Prefer RLE over raw full-frame storage.
- Drop walk frames before dropping idle/blink/happy/nap/alert frames.
- Reduce polished targets to MVP counts for rare behaviors.

Keep RAM use fixed: decode directly into the existing display framebuffer and
do not allocate a second full-screen buffer.

## Phase 6: Host Rename And Prompt Alignment

The host already loads the pet name and prompt from `PET.md`, so most host code
should keep working after the spec update.

Tasks:

- Update tests that assert "Mochi" or Shiba-specific wording.
- Keep `host/protocol.py` unchanged unless the behavior vocabulary changes.
- Keep `host/body.py` unchanged unless a Shiro pose exposes a real mismatch.
- Keep backend, model family, and preset configuration separate.
- Keep Ollama/qwen defaults unchanged.

The host should continue to send high-level behavior frames:

```json
{"v":1,"kind":"behavior","mood":"happy","animation":"happy","text":"tail party","alert":false,"duration_ms":3500}
```

## Phase 7: Hardware Smoke Tests

Do not wait until all animations are done before testing on hardware.

Run these checks on the Waveshare 1.5 inch 128x128 RGB OLED:

- Display one static Shiro sprite over black.
- Confirm white body visibility.
- Confirm black outline readability.
- Confirm red collar visibility.
- Confirm no green spill or alpha artifacts.
- Confirm cube mirror/reflection orientation.
- Confirm sprite scale and centering.
- Confirm optional text bubble does not obscure the face.
- Confirm alert border does not overpower the sprite.
- Confirm local idle still runs with host disconnected.
- Confirm Wi-Fi behavior updates switch poses correctly.

If the sprite is too small or too faint, fix the art scale and palette before
adding more frames.

## Phase 8: Verification

Firmware commands, from `device/`:

```sh
cargo build
cargo fmt --all -- --check
cargo clippy --all-features --workspace -- -D warnings
```

Host checks:

```sh
python -m unittest
```

Add or update tests for:

- PET prompt/name extraction after the rename.
- Behavior validation remains unchanged.
- Prompt-to-behavior mapping still uses the closed vocabulary.
- Sprite metadata parsing if the converter lives in host/tools.
- Converter output determinism.

## Open Decisions

- Whether the generated sprite sheet is good enough as-is or needs manual
  cleanup before firmware conversion.
- Whether to keep the pet name literally `Shiro` in prompts or use a private
  variant while keeping the visual faithful.
- Whether `walk` should remain in V1 if a good side-profile cycle takes too long.
- Whether text bubbles should stay once Shiro is on screen, since the face is
  more important than text.

## Definition Of Done

- `PET.md` describes Shiro and no longer describes Mochi/Shiba as the active
  pet.
- Host tests pass.
- Firmware builds, formats, and clippy-checks from `device/`.
- The device renders Shiro sprites instead of geometric Mochi shapes.
- Local idle works with no host connected.
- Wi-Fi behavior updates still use newline-delimited JSON with `v: 1`.
- The OLED/cube smoke test confirms Shiro is correctly oriented, readable, and
  cute at 128x128.
