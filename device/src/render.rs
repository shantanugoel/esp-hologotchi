//! Shiro's sprite renderer and local idle loop.
//!
//! The ESP32-C3 never decodes PNGs. Shiro's hand-drawn poses are generated into
//! fixed transparent/solid RLE streams by `host/tools/convert_sprite_sheet.py`,
//! then blitted directly into the display framebuffer with small integer-only
//! pose offsets for breathing, wagging, bouncing, and alert pulses.

use crate::behavior::{Animation, BehaviorUpdate};
use crate::shiro_sprites::{self, PALETTE_RGB565, PoseId, RUN_LEN_MASK, SOLID_RUN_FLAG, Sprite};
use embedded_graphics::mono_font::MonoTextStyle;
use embedded_graphics::mono_font::ascii::FONT_6X10;
use embedded_graphics::pixelcolor::Rgb565;
use embedded_graphics::prelude::*;
use embedded_graphics::primitives::{
    PrimitiveStyle, PrimitiveStyleBuilder, Rectangle, RoundedRectangle,
};
use embedded_graphics::text::{Baseline, Text};

/// `sin(2*pi * i / 64) * 1024`, integer-only motion source.
const SIN: [i16; 64] = [
    0, 100, 200, 297, 392, 483, 569, 650, 724, 792, 851, 903, 946, 980, 1004, 1019, 1024, 1019,
    1004, 980, 946, 903, 851, 792, 724, 650, 569, 483, 392, 297, 200, 100, 0, -100, -200, -297,
    -392, -483, -569, -650, -724, -792, -851, -903, -946, -980, -1004, -1019, -1024, -1019, -1004,
    -980, -946, -903, -851, -792, -724, -650, -569, -483, -392, -297, -200, -100,
];

/// Frames between local idle blinks (~4.8 s at 20 fps).
const BLINK_PERIOD: u32 = 96;
/// How many frames a local idle blink stays shut.
const BLINK_LEN: u32 = 3;
/// A curious look-around every ~14 seconds at 20 fps.
const LOOK_PERIOD: u32 = 280;
const LOOK_START: u32 = 150;
const LOOK_LEN: u32 = 42;
/// Half of the temporary patrol loop.
const WALK_HALF_PERIOD: u32 = 110;

const BUBBLE_FILL: Rgb565 = Rgb565::new(31, 63, 30);
const BUBBLE_STROKE: Rgb565 = Rgb565::new(16, 32, 16);
const TEXT: Rgb565 = Rgb565::new(0, 0, 0);
const ALERT: Rgb565 = Rgb565::new(31, 8, 4);
const ALERT_SOFT: Rgb565 = Rgb565::new(31, 30, 10);

const LOOK_LOOP: [TimedPose; 5] = [
    TimedPose::new(PoseId::Listening, 10, 0, 0),
    TimedPose::new(PoseId::LookingLeft, 16, -1, 0),
    TimedPose::new(PoseId::Listening, 8, 0, 0),
    TimedPose::new(PoseId::LookingRight, 16, 1, 0),
    TimedPose::new(PoseId::SittingIdle, 10, 0, 0),
];
const HAPPY_LOOP: [TimedPose; 4] = [
    TimedPose::new(PoseId::TailWagLeft, 6, -1, 0),
    TimedPose::new(PoseId::TailWagRight, 6, 1, 0),
    TimedPose::new(PoseId::HappyBounce, 7, 0, -2),
    TimedPose::new(PoseId::TailWagRight, 5, 1, -1),
];
const EXCITED_LOOP: [TimedPose; 4] = [
    TimedPose::new(PoseId::HappyBounce, 5, 0, 0),
    TimedPose::new(PoseId::ExcitedJump, 5, 0, 1),
    TimedPose::new(PoseId::HappyBounce, 4, 0, -2),
    TimedPose::new(PoseId::ExcitedJump, 6, 0, 1),
];
const ALERT_LOOP: [TimedPose; 2] = [
    TimedPose::new(PoseId::AlertEarsUp, 7, 0, -1),
    TimedPose::new(PoseId::Surprised, 7, 0, 0),
];

/// Shiro's render state.
pub struct Scene {
    frame: u32,
    active: Option<ActiveBehavior>,
}

#[derive(Clone, Debug)]
struct ActiveBehavior {
    behavior: BehaviorUpdate,
    frames_left: u16,
    elapsed: u16,
}

#[derive(Clone, Copy)]
struct SpriteFrame {
    pose: PoseId,
    x: i32,
    y: i32,
    alert_border: bool,
}

#[derive(Clone, Copy)]
struct TimedPose {
    pose: PoseId,
    frames: u8,
    x: i32,
    y: i32,
}

impl TimedPose {
    const fn new(pose: PoseId, frames: u8, x: i32, y: i32) -> Self {
        Self { pose, frames, x, y }
    }
}

impl Default for Scene {
    fn default() -> Self {
        Self::new()
    }
}

impl Scene {
    /// Create a fresh scene with only the local idle loop active.
    pub fn new() -> Self {
        Self {
            frame: 0,
            active: None,
        }
    }

    /// Apply a new host behavior. It takes over until its duration elapses, then
    /// Shiro falls back to the local idle loop.
    pub fn apply_behavior(&mut self, behavior: BehaviorUpdate, frame_ms: u32) {
        self.active = Some(ActiveBehavior {
            frames_left: behavior.frames_for(frame_ms),
            elapsed: 0,
            behavior,
        });
    }

    /// Advance the renderer by one frame.
    pub fn tick(&mut self) {
        self.frame = self.frame.wrapping_add(1);

        if let Some(active) = self.active.as_mut() {
            active.elapsed = active.elapsed.saturating_add(1);
            if active.frames_left > 1 {
                active.frames_left -= 1;
            } else {
                self.active = None;
            }
        }
    }

    /// Render the current frame into `target`.
    pub fn draw<D>(&self, target: &mut D) -> Result<(), D::Error>
    where
        D: DrawTarget<Color = Rgb565>,
    {
        target.clear(Rgb565::BLACK)?;

        let (animation, anim_frame, text, alert) = if let Some(active) = self.active.as_ref() {
            (
                active.behavior.animation,
                active.elapsed as u32,
                active.behavior.text.as_ref().map(|text| text.as_str()),
                active.behavior.alert,
            )
        } else {
            let animation = self.local_animation();
            let anim_frame = match animation {
                Animation::Blink => self.frame % BLINK_PERIOD,
                Animation::LookAround => (self.frame % LOOK_PERIOD).saturating_sub(LOOK_START),
                _ => self.frame,
            };
            (animation, anim_frame, None, false)
        };

        let frame = frame_for(animation, self.frame, anim_frame, alert);
        blit_sprite(target, frame)?;
        if let Some(text) = text {
            draw_bubble(target, text, alert)?;
        }
        if frame.alert_border {
            draw_alert_border(target, anim_frame)?;
        }

        Ok(())
    }

    fn local_animation(&self) -> Animation {
        if self.frame % BLINK_PERIOD < BLINK_LEN {
            Animation::Blink
        } else {
            let look_phase = self.frame % LOOK_PERIOD;
            if (LOOK_START..LOOK_START + LOOK_LEN).contains(&look_phase) {
                Animation::LookAround
            } else {
                Animation::Idle
            }
        }
    }
}

fn frame_for(
    animation: Animation,
    global_frame: u32,
    anim_frame: u32,
    force_alert: bool,
) -> SpriteFrame {
    let mut frame = SpriteFrame {
        pose: PoseId::SittingIdle,
        x: 0,
        y: wave(global_frame, 1, 0, 1),
        alert_border: force_alert,
    };

    match animation {
        Animation::Idle => {}
        Animation::Blink => {
            frame.pose = if anim_frame % 18 < 3 {
                PoseId::Blink
            } else {
                PoseId::SittingIdle
            };
            frame.y = wave(global_frame, 1, 8, 1);
        }
        Animation::LookAround => {
            let timed = timed_pose(anim_frame, &LOOK_LOOP);
            frame.pose = timed.pose;
            frame.x = timed.x;
            frame.y = timed.y + wave(global_frame, 1, 16, 1);
        }
        Animation::Confused => {
            frame.pose = PoseId::ConfusedHeadTilt;
            frame.x = wave(anim_frame, 5, 0, 2);
            frame.y = wave(anim_frame, 2, 16, 1);
        }
        Animation::Walk => {
            let walk_phase = anim_frame % (WALK_HALF_PERIOD * 2);
            let walk_step = walk_phase % WALK_HALF_PERIOD;
            if walk_phase < WALK_HALF_PERIOD {
                frame.pose = PoseId::LookingRight;
                frame.x = -18 + (walk_step as i32 * 36) / (WALK_HALF_PERIOD as i32 - 1);
            } else {
                frame.pose = PoseId::LookingLeft;
                frame.x = 18 - (walk_step as i32 * 36) / (WALK_HALF_PERIOD as i32 - 1);
            }
            frame.y = wave_abs(anim_frame, 4, 0, 1);
        }
        Animation::Happy => {
            let timed = timed_pose(anim_frame, &HAPPY_LOOP);
            frame.pose = timed.pose;
            frame.x = timed.x + wave(anim_frame, 2, 0, 1);
            frame.y = timed.y - wave_abs(anim_frame, 4, 16, 1);
        }
        Animation::Play => {
            frame.pose = PoseId::PawLift;
            frame.x = wave(anim_frame, 3, 0, 1);
            frame.y = wave(anim_frame, 4, 16, 1);
        }
        Animation::Excited => {
            let timed = timed_pose(anim_frame, &EXCITED_LOOP);
            frame.pose = timed.pose;
            frame.x = timed.x + wave(anim_frame, 4, 0, 1);
            frame.y = timed.y;
        }
        Animation::Sleepy => {
            frame.pose = PoseId::RelaxedLoaf;
            frame.y = 1 + wave(anim_frame, 1, 16, 1);
        }
        Animation::Nap => {
            frame.pose = PoseId::SleepingCurled;
            frame.x = wave(anim_frame, 1, 0, 1);
            frame.y = wave(anim_frame, 1, 16, 1);
        }
        Animation::Worried => {
            frame.pose = PoseId::SadDroop;
            frame.x = wave(anim_frame, 3, 0, 1);
            frame.y = 1 + wave(anim_frame, 1, 16, 1);
        }
        Animation::Alert => {
            let timed = timed_pose(anim_frame, &ALERT_LOOP);
            frame.pose = timed.pose;
            frame.x = timed.x + wave(anim_frame, 7, 0, 1);
            frame.y = timed.y - wave_abs(anim_frame, 7, 16, 1);
            frame.alert_border = true;
        }
    }

    frame
}

fn timed_pose(frame: u32, poses: &[TimedPose]) -> TimedPose {
    let total: u32 = poses.iter().map(|pose| pose.frames as u32).sum();
    let mut phase = frame % total.max(1);
    for pose in poses {
        let frames = pose.frames as u32;
        if phase < frames {
            return *pose;
        }
        phase -= frames;
    }
    poses[0]
}

fn blit_sprite<D>(target: &mut D, frame: SpriteFrame) -> Result<(), D::Error>
where
    D: DrawTarget<Color = Rgb565>,
{
    let sprite = shiro_sprites::sprite(frame.pose);
    let origin = Point::new(frame.x + sprite.x as i32, frame.y + sprite.y as i32);
    target.draw_iter(SpritePixels::new(sprite, origin))
}

struct SpritePixels<'a> {
    sprite: &'a Sprite,
    origin: Point,
    data_pos: usize,
    pixel_index: usize,
    solid_left: usize,
    solid_color: Rgb565,
}

impl<'a> SpritePixels<'a> {
    fn new(sprite: &'a Sprite, origin: Point) -> Self {
        Self {
            sprite,
            origin,
            data_pos: 0,
            pixel_index: 0,
            solid_left: 0,
            solid_color: Rgb565::BLACK,
        }
    }
}

impl Iterator for SpritePixels<'_> {
    type Item = Pixel<Rgb565>;

    fn next(&mut self) -> Option<Self::Item> {
        let width = self.sprite.width as usize;
        let total = width * self.sprite.height as usize;

        while self.pixel_index < total {
            if self.solid_left > 0 {
                let local = self.pixel_index;
                self.pixel_index += 1;
                self.solid_left -= 1;
                let x = (local % width) as i32;
                let y = (local / width) as i32;
                return Some(Pixel(
                    Point::new(self.origin.x + x, self.origin.y + y),
                    self.solid_color,
                ));
            }

            if self.data_pos >= self.sprite.data.len() {
                return None;
            }
            let command = self.sprite.data[self.data_pos];
            self.data_pos += 1;
            let run_len = ((command & RUN_LEN_MASK) as usize) + 1;
            if command & SOLID_RUN_FLAG == 0 {
                self.pixel_index = self.pixel_index.saturating_add(run_len);
                continue;
            }

            let palette_index = self.sprite.data.get(self.data_pos).copied()? as usize;
            self.data_pos += 1;
            let rgb565 = PALETTE_RGB565.get(palette_index).copied().unwrap_or(0);
            self.solid_color = rgb565_color(rgb565);
            self.solid_left = run_len;
        }

        None
    }
}

fn draw_bubble<D>(target: &mut D, text: &str, alert: bool) -> Result<(), D::Error>
where
    D: DrawTarget<Color = Rgb565>,
{
    const MAX_LINE_CHARS: usize = 17;
    const TEXT_W: i32 = 6;
    const LINE_H: i32 = 10;
    const PAD_X: i32 = 7;
    const PAD_Y: i32 = 4;

    let split = split_bubble_text(text, MAX_LINE_CHARS);
    let line_count = if split.second.is_some() { 2 } else { 1 };
    let longest = split.first.chars().count().max(
        split
            .second
            .map(|line| line.chars().count())
            .unwrap_or_default(),
    ) as i32;
    let width = (longest * TEXT_W + PAD_X * 2).clamp(36, 122);
    let height = line_count * LINE_H + PAD_Y * 2;
    let bubble_x = (128 - width) / 2;
    let bubble_y = 128 - height - 2;
    let stroke = if alert { ALERT } else { BUBBLE_STROKE };
    let style = PrimitiveStyleBuilder::new()
        .fill_color(BUBBLE_FILL)
        .stroke_color(stroke)
        .stroke_width(2)
        .build();
    RoundedRectangle::with_equal_corners(
        Rectangle::new(
            Point::new(bubble_x, bubble_y),
            Size::new(width as u32, height as u32),
        ),
        Size::new(8, 8),
    )
    .into_styled(style)
    .draw(target)?;

    let text_style = MonoTextStyle::new(&FONT_6X10, TEXT);
    let text_x = bubble_x + PAD_X;
    let text_y = bubble_y + PAD_Y;
    Text::with_baseline(
        split.first,
        Point::new(text_x, text_y),
        text_style,
        Baseline::Top,
    )
    .draw(target)
    .map(|_| ())?;
    if let Some(second) = split.second {
        Text::with_baseline(
            second,
            Point::new(text_x, text_y + LINE_H),
            text_style,
            Baseline::Top,
        )
        .draw(target)
        .map(|_| ())?;
    }

    Ok(())
}

struct BubbleText<'a> {
    first: &'a str,
    second: Option<&'a str>,
}

fn split_bubble_text(text: &str, max_line_chars: usize) -> BubbleText<'_> {
    if text.chars().count() <= max_line_chars {
        return BubbleText {
            first: text,
            second: None,
        };
    }

    let mut split_at = None;
    let mut byte_at_limit = text.len();
    for (char_index, (byte_index, ch)) in text.char_indices().enumerate() {
        if char_index == max_line_chars {
            byte_at_limit = byte_index;
            break;
        }
        if ch == ' ' {
            split_at = Some(byte_index);
        }
    }

    let split_at = split_at.unwrap_or(byte_at_limit);
    let first = text[..split_at].trim_end();
    let second = text[split_at..].trim_start();
    BubbleText {
        first,
        second: if second.is_empty() {
            None
        } else {
            Some(second)
        },
    }
}

fn draw_alert_border<D>(target: &mut D, frame: u32) -> Result<(), D::Error>
where
    D: DrawTarget<Color = Rgb565>,
{
    let width = 1 + wave_abs(frame, 7, 0, 2) as u32;
    RoundedRectangle::with_equal_corners(
        Rectangle::new(Point::new(1, 1), Size::new(126, 126)),
        Size::new(10, 10),
    )
    .into_styled(PrimitiveStyle::with_stroke(ALERT, width))
    .draw(target)?;
    RoundedRectangle::with_equal_corners(
        Rectangle::new(Point::new(4, 4), Size::new(120, 120)),
        Size::new(8, 8),
    )
    .into_styled(PrimitiveStyle::with_stroke(ALERT_SOFT, 1))
    .draw(target)
}

#[inline]
fn rgb565_color(word: u16) -> Rgb565 {
    Rgb565::new(
        ((word >> 11) & 0x1F) as u8,
        ((word >> 5) & 0x3F) as u8,
        (word & 0x1F) as u8,
    )
}

#[inline]
fn wave(frame: u32, speed: u32, phase: usize, amplitude: i32) -> i32 {
    let idx = ((frame.wrapping_mul(speed) as usize) + phase) & 63;
    (SIN[idx] as i32 * amplitude) / 1024
}

#[inline]
fn wave_abs(frame: u32, speed: u32, phase: usize, amplitude: i32) -> i32 {
    wave(frame, speed, phase, amplitude).abs()
}
