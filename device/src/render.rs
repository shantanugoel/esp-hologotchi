//! Mochi's renderer and local idle loop.
//!
//! Phase 4 keeps the original local aliveness but lets the host temporarily steer
//! Mochi with high-level behaviors from the Wi-Fi control socket. The renderer
//! stays integer-only and cheap: pose changes come from a few lookup-table-driven
//! offsets and simple shape swaps rather than extra assets or floating point.

use crate::behavior::{Animation, BehaviorUpdate};
use embedded_graphics::mono_font::MonoTextStyle;
use embedded_graphics::mono_font::ascii::FONT_6X10;
use embedded_graphics::pixelcolor::Rgb565;
use embedded_graphics::prelude::*;
use embedded_graphics::primitives::{
    Circle, Ellipse, Line, PrimitiveStyle, PrimitiveStyleBuilder, Rectangle, RoundedRectangle,
    Triangle,
};
use embedded_graphics::text::{Baseline, Text};

/// `sin(2*pi * i / 64) * 1024`, integer-only motion source.
const SIN: [i16; 64] = [
    0, 100, 200, 297, 392, 483, 569, 650, 724, 792, 851, 903, 946, 980, 1004, 1019, 1024, 1019,
    1004, 980, 946, 903, 851, 792, 724, 650, 569, 483, 392, 297, 200, 100, 0, -100, -200, -297,
    -392, -483, -569, -650, -724, -792, -851, -903, -946, -980, -1004, -1019, -1024, -1019, -1004,
    -980, -946, -903, -851, -792, -724, -650, -569, -483, -392, -297, -200, -100,
];

/// Frames between blinks during the local idle loop (~5.5 s at 20 fps).
const BLINK_PERIOD: u32 = 110;
/// How many frames an eye stays shut (~0.2 s at 20 fps).
const BLINK_LEN: u32 = 4;
/// A curious head-turn every ~18 seconds at 20 fps.
const LOOK_PERIOD: u32 = 360;
const LOOK_START: u32 = 220;
const LOOK_LEN: u32 = 28;

// Mochi's palette keeps enough contrast to read through the cube while preserving
// the natural Shiba look locked in `PET.md`.
const ORANGE: Rgb565 = Rgb565::new(25, 31, 7);
const ORANGE_DK: Rgb565 = Rgb565::new(19, 22, 4);
const CREAM: Rgb565 = Rgb565::new(29, 56, 23);
const PINK: Rgb565 = Rgb565::new(27, 43, 18);
const BLUSH: Rgb565 = Rgb565::new(29, 39, 17);
const NAVY: Rgb565 = Rgb565::new(3, 7, 12);
const SHINE: Rgb565 = Rgb565::new(31, 63, 31);
const ALERT: Rgb565 = Rgb565::new(31, 16, 4);
const ALERT_SOFT: Rgb565 = Rgb565::new(31, 31, 10);

/// Mochi's render state.
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
struct Pose {
    ox: i32,
    oy: i32,
    hx: i32,
    hy: i32,
    tail_x: i32,
    tail_y: i32,
    ear_drop: i32,
    ear_spread: i32,
    eye_shift: i32,
    eye_y: i32,
    eye_style: EyeStyle,
    mouth: MouthStyle,
    alert_border: bool,
}

#[derive(Clone, Copy)]
enum EyeStyle {
    Open,
    Blink,
    Sleepy,
    Worried,
    Alert,
}

#[derive(Clone, Copy)]
enum MouthStyle {
    Smile,
    Grin,
    Flat,
    Frown,
    Yawn,
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
    /// Mochi falls back to the local idle loop.
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
        let pose = pose_for(animation, self.frame, anim_frame, alert);

        draw_mochi(target, pose)?;
        if let Some(text) = text {
            draw_bubble(target, text, alert)?;
        }
        if pose.alert_border {
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

fn pose_for(animation: Animation, global_frame: u32, anim_frame: u32, force_alert: bool) -> Pose {
    let mut pose = Pose {
        ox: wave(global_frame, 1, 16, 1),
        oy: wave(global_frame, 1, 0, 3),
        hx: 0,
        hy: 0,
        tail_x: 0,
        tail_y: 0,
        ear_drop: 0,
        ear_spread: 0,
        eye_shift: 0,
        eye_y: 0,
        eye_style: EyeStyle::Open,
        mouth: MouthStyle::Smile,
        alert_border: false,
    };

    match animation {
        Animation::Idle => {}
        Animation::Blink => {
            pose.eye_style = if anim_frame % 18 < 4 {
                EyeStyle::Blink
            } else {
                EyeStyle::Open
            };
        }
        Animation::LookAround => {
            let glance = wave(anim_frame, 3, 0, 6);
            pose.eye_shift = glance;
            pose.hx = glance / 2;
            pose.ear_drop = -2;
        }
        Animation::Happy => {
            pose.oy -= wave_abs(anim_frame, 4, 0, 4);
            pose.tail_x = wave(anim_frame, 5, 0, 7);
            pose.tail_y = -wave_abs(anim_frame, 5, 0, 3);
            pose.ear_drop = -4;
            pose.mouth = MouthStyle::Grin;
        }
        Animation::Sleepy => {
            pose.oy += 2;
            pose.hy += 2;
            pose.ear_drop = 7;
            pose.ear_spread = 5;
            pose.eye_style = if anim_frame % 54 < 5 {
                EyeStyle::Blink
            } else {
                EyeStyle::Sleepy
            };
            pose.mouth = if (18..30).contains(&(anim_frame % 96)) {
                MouthStyle::Yawn
            } else {
                MouthStyle::Flat
            };
        }
        Animation::Worried => {
            pose.hy += 1;
            pose.tail_x = -4;
            pose.tail_y = 2;
            pose.ear_drop = 9;
            pose.ear_spread = 8;
            pose.eye_style = EyeStyle::Worried;
            pose.mouth = MouthStyle::Frown;
        }
        Animation::Alert => {
            pose.oy -= wave_abs(anim_frame, 6, 0, 2);
            pose.ear_drop = -7;
            pose.eye_style = EyeStyle::Alert;
            pose.mouth = MouthStyle::Yawn;
            pose.alert_border = true;
        }
    }

    if force_alert {
        pose.alert_border = true;
    }

    pose
}

fn draw_mochi<D>(target: &mut D, pose: Pose) -> Result<(), D::Error>
where
    D: DrawTarget<Color = Rgb565>,
{
    let body_ox = pose.ox;
    let body_oy = pose.oy;
    let head_ox = pose.ox + pose.hx;
    let head_oy = pose.oy + pose.hy;
    let tail_ox = pose.ox + pose.tail_x;
    let tail_oy = pose.oy + pose.tail_y;

    // --- Body ---
    fill_circle(target, 103 + tail_ox, 88 + tail_oy, 30, ORANGE)?;
    stroke_circle(target, 105 + tail_ox, 90 + tail_oy, 15, ORANGE_DK, 3)?;
    fill_circle(target, 105 + tail_ox, 90 + tail_oy, 6, CREAM)?;

    fill_circle(target, 64 + body_ox, 100 + body_oy, 76, ORANGE)?;
    fill_ellipse(target, 64 + body_ox, 102 + body_oy, 48, 54, CREAM)?;
    fill_round_rect(target, 45 + body_ox, 96 + body_oy, 17, 34, 8, CREAM)?;
    fill_round_rect(target, 66 + body_ox, 96 + body_oy, 17, 34, 8, CREAM)?;
    stroke_line(
        target,
        pt(64, 112, body_ox, body_oy),
        pt(64, 128, body_ox, body_oy),
        2,
        ORANGE_DK,
    )?;
    stroke_line(
        target,
        pt(50, 122, body_ox, body_oy),
        pt(50, 128, body_ox, body_oy),
        2,
        ORANGE_DK,
    )?;
    stroke_line(
        target,
        pt(57, 122, body_ox, body_oy),
        pt(57, 128, body_ox, body_oy),
        2,
        ORANGE_DK,
    )?;
    stroke_line(
        target,
        pt(72, 122, body_ox, body_oy),
        pt(72, 128, body_ox, body_oy),
        2,
        ORANGE_DK,
    )?;
    stroke_line(
        target,
        pt(79, 122, body_ox, body_oy),
        pt(79, 128, body_ox, body_oy),
        2,
        ORANGE_DK,
    )?;

    // --- Head ---
    let ear_drop = pose.ear_drop;
    let ear_spread = pose.ear_spread;
    fill_triangle(
        target,
        pt(57, 31 + ear_drop / 2, head_ox, head_oy),
        pt(41 - ear_spread / 2, 9 + ear_drop, head_ox, head_oy),
        pt(35 - ear_spread, 37 + ear_drop / 2, head_ox, head_oy),
        ORANGE,
    )?;
    fill_triangle(
        target,
        pt(71, 31 + ear_drop / 2, head_ox, head_oy),
        pt(87 + ear_spread / 2, 9 + ear_drop, head_ox, head_oy),
        pt(93 + ear_spread, 37 + ear_drop / 2, head_ox, head_oy),
        ORANGE,
    )?;

    fill_circle(target, 64 + head_ox, 47 + head_oy, 66, ORANGE)?;

    fill_triangle(
        target,
        pt(53, 30 + ear_drop / 2, head_ox, head_oy),
        pt(46 - ear_spread / 3, 19 + ear_drop / 2, head_ox, head_oy),
        pt(44 - ear_spread / 3, 34 + ear_drop / 2, head_ox, head_oy),
        PINK,
    )?;
    fill_triangle(
        target,
        pt(75, 30 + ear_drop / 2, head_ox, head_oy),
        pt(82 + ear_spread / 3, 19 + ear_drop / 2, head_ox, head_oy),
        pt(84 + ear_spread / 3, 34 + ear_drop / 2, head_ox, head_oy),
        PINK,
    )?;

    fill_ellipse(target, 64 + head_ox, 67 + head_oy, 50, 40, CREAM)?;
    fill_circle(target, 50 + head_ox, 33 + head_oy, 7, CREAM)?;
    fill_circle(target, 78 + head_ox, 33 + head_oy, 7, CREAM)?;
    fill_ellipse(target, 46 + head_ox, 70 + head_oy, 12, 7, BLUSH)?;
    fill_ellipse(target, 82 + head_ox, 70 + head_oy, 12, 7, BLUSH)?;

    draw_eyes(
        target,
        head_ox,
        head_oy + pose.eye_y,
        pose.eye_shift,
        pose.eye_style,
    )?;
    draw_mouth(target, head_ox, head_oy, pose.mouth)?;

    Ok(())
}

fn draw_eyes<D>(
    target: &mut D,
    ox: i32,
    oy: i32,
    shift: i32,
    style: EyeStyle,
) -> Result<(), D::Error>
where
    D: DrawTarget<Color = Rgb565>,
{
    let (lx, rx, ey) = (52 + shift, 76 + shift, 52);

    match style {
        EyeStyle::Open => {
            fill_circle(target, lx + ox, ey + oy, 16, NAVY)?;
            fill_circle(target, rx + ox, ey + oy, 16, NAVY)?;
            fill_circle(target, lx - 3 + ox, ey - 4 + oy, 6, SHINE)?;
            fill_circle(target, rx - 3 + ox, ey - 4 + oy, 6, SHINE)?;
            fill_circle(target, lx + 4 + ox, ey + 4 + oy, 3, SHINE)?;
            fill_circle(target, rx + 4 + ox, ey + 4 + oy, 3, SHINE)?;
        }
        EyeStyle::Blink => {
            stroke_line(
                target,
                pt(lx - 8, ey, ox, oy),
                pt(lx + 8, ey, ox, oy),
                3,
                NAVY,
            )?;
            stroke_line(
                target,
                pt(rx - 8, ey, ox, oy),
                pt(rx + 8, ey, ox, oy),
                3,
                NAVY,
            )?;
        }
        EyeStyle::Sleepy => {
            fill_ellipse(target, lx + ox, ey + oy + 2, 18, 10, NAVY)?;
            fill_ellipse(target, rx + ox, ey + oy + 2, 18, 10, NAVY)?;
            stroke_line(
                target,
                pt(lx - 8, ey - 1, ox, oy),
                pt(lx + 8, ey - 1, ox, oy),
                2,
                SHINE,
            )?;
            stroke_line(
                target,
                pt(rx - 8, ey - 1, ox, oy),
                pt(rx + 8, ey - 1, ox, oy),
                2,
                SHINE,
            )?;
        }
        EyeStyle::Worried => {
            fill_ellipse(target, lx + ox, ey + oy, 16, 18, NAVY)?;
            fill_ellipse(target, rx + ox, ey + oy, 16, 18, NAVY)?;
            stroke_line(
                target,
                pt(lx - 10, ey - 10, ox, oy),
                pt(lx + 4, ey - 7, ox, oy),
                2,
                NAVY,
            )?;
            stroke_line(
                target,
                pt(rx - 4, ey - 7, ox, oy),
                pt(rx + 10, ey - 10, ox, oy),
                2,
                NAVY,
            )?;
            fill_circle(target, lx - 2 + ox, ey - 3 + oy, 4, SHINE)?;
            fill_circle(target, rx - 2 + ox, ey - 3 + oy, 4, SHINE)?;
        }
        EyeStyle::Alert => {
            fill_circle(target, lx + ox, ey + oy, 18, NAVY)?;
            fill_circle(target, rx + ox, ey + oy, 18, NAVY)?;
            fill_circle(target, lx - 4 + ox, ey - 5 + oy, 6, SHINE)?;
            fill_circle(target, rx - 4 + ox, ey - 5 + oy, 6, SHINE)?;
            stroke_line(
                target,
                pt(lx - 7, ey - 12, ox, oy),
                pt(lx + 7, ey - 12, ox, oy),
                2,
                ALERT_SOFT,
            )?;
            stroke_line(
                target,
                pt(rx - 7, ey - 12, ox, oy),
                pt(rx + 7, ey - 12, ox, oy),
                2,
                ALERT_SOFT,
            )?;
        }
    }

    Ok(())
}

fn draw_mouth<D>(target: &mut D, ox: i32, oy: i32, style: MouthStyle) -> Result<(), D::Error>
where
    D: DrawTarget<Color = Rgb565>,
{
    fill_triangle(
        target,
        pt(58, 62, ox, oy),
        pt(70, 62, ox, oy),
        pt(64, 71, ox, oy),
        NAVY,
    )?;
    fill_circle(target, 61 + ox, 64 + oy, 3, SHINE)?;

    match style {
        MouthStyle::Smile => {
            stroke_line(target, pt(64, 71, ox, oy), pt(64, 76, ox, oy), 2, NAVY)?;
            stroke_line(target, pt(64, 76, ox, oy), pt(57, 79, ox, oy), 2, NAVY)?;
            stroke_line(target, pt(57, 79, ox, oy), pt(53, 75, ox, oy), 2, NAVY)?;
            stroke_line(target, pt(64, 76, ox, oy), pt(71, 79, ox, oy), 2, NAVY)?;
            stroke_line(target, pt(71, 79, ox, oy), pt(75, 75, ox, oy), 2, NAVY)?;
        }
        MouthStyle::Grin => {
            stroke_line(target, pt(64, 71, ox, oy), pt(64, 77, ox, oy), 2, NAVY)?;
            stroke_line(target, pt(64, 77, ox, oy), pt(55, 81, ox, oy), 3, NAVY)?;
            stroke_line(target, pt(64, 77, ox, oy), pt(73, 81, ox, oy), 3, NAVY)?;
        }
        MouthStyle::Flat => {
            stroke_line(target, pt(64, 71, ox, oy), pt(64, 75, ox, oy), 2, NAVY)?;
            stroke_line(target, pt(58, 78, ox, oy), pt(70, 78, ox, oy), 2, NAVY)?;
        }
        MouthStyle::Frown => {
            stroke_line(target, pt(64, 71, ox, oy), pt(64, 76, ox, oy), 2, NAVY)?;
            stroke_line(target, pt(64, 76, ox, oy), pt(56, 74, ox, oy), 2, NAVY)?;
            stroke_line(target, pt(64, 76, ox, oy), pt(72, 74, ox, oy), 2, NAVY)?;
        }
        MouthStyle::Yawn => {
            stroke_line(target, pt(64, 71, ox, oy), pt(64, 75, ox, oy), 2, NAVY)?;
            fill_ellipse(target, 64 + ox, 79 + oy, 12, 10, NAVY)?;
            fill_ellipse(target, 64 + ox, 80 + oy, 7, 5, BLUSH)?;
        }
    }

    Ok(())
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
    let bubble_y = 2;
    let stroke = if alert { ALERT } else { ORANGE };
    let style = PrimitiveStyleBuilder::new()
        .fill_color(CREAM)
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

    let text_style = MonoTextStyle::new(&FONT_6X10, NAVY);
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
fn wave(frame: u32, speed: u32, phase: usize, amplitude: i32) -> i32 {
    let idx = ((frame.wrapping_mul(speed) as usize) + phase) & 63;
    (SIN[idx] as i32 * amplitude) / 1024
}

#[inline]
fn wave_abs(frame: u32, speed: u32, phase: usize, amplitude: i32) -> i32 {
    wave(frame, speed, phase, amplitude).abs()
}

/// Offset a base coordinate by the current animation amount.
#[inline]
fn pt(x: i32, y: i32, ox: i32, oy: i32) -> Point {
    Point::new(x + ox, y + oy)
}

fn fill_circle<D>(target: &mut D, cx: i32, cy: i32, dia: i32, color: Rgb565) -> Result<(), D::Error>
where
    D: DrawTarget<Color = Rgb565>,
{
    let top_left = Point::new(cx - dia / 2, cy - dia / 2);
    Circle::new(top_left, dia as u32)
        .into_styled(PrimitiveStyle::with_fill(color))
        .draw(target)
}

fn stroke_circle<D>(
    target: &mut D,
    cx: i32,
    cy: i32,
    dia: i32,
    color: Rgb565,
    width: u32,
) -> Result<(), D::Error>
where
    D: DrawTarget<Color = Rgb565>,
{
    let top_left = Point::new(cx - dia / 2, cy - dia / 2);
    Circle::new(top_left, dia as u32)
        .into_styled(PrimitiveStyle::with_stroke(color, width))
        .draw(target)
}

fn fill_ellipse<D>(
    target: &mut D,
    cx: i32,
    cy: i32,
    w: i32,
    h: i32,
    color: Rgb565,
) -> Result<(), D::Error>
where
    D: DrawTarget<Color = Rgb565>,
{
    let top_left = Point::new(cx - w / 2, cy - h / 2);
    Ellipse::new(top_left, Size::new(w as u32, h as u32))
        .into_styled(PrimitiveStyle::with_fill(color))
        .draw(target)
}

fn fill_round_rect<D>(
    target: &mut D,
    x: i32,
    y: i32,
    w: i32,
    h: i32,
    r: i32,
    color: Rgb565,
) -> Result<(), D::Error>
where
    D: DrawTarget<Color = Rgb565>,
{
    RoundedRectangle::with_equal_corners(
        Rectangle::new(Point::new(x, y), Size::new(w as u32, h as u32)),
        Size::new(r as u32, r as u32),
    )
    .into_styled(PrimitiveStyle::with_fill(color))
    .draw(target)
}

fn fill_triangle<D>(
    target: &mut D,
    p1: Point,
    p2: Point,
    p3: Point,
    color: Rgb565,
) -> Result<(), D::Error>
where
    D: DrawTarget<Color = Rgb565>,
{
    Triangle::new(p1, p2, p3)
        .into_styled(PrimitiveStyle::with_fill(color))
        .draw(target)
}

fn stroke_line<D>(
    target: &mut D,
    a: Point,
    b: Point,
    width: u32,
    color: Rgb565,
) -> Result<(), D::Error>
where
    D: DrawTarget<Color = Rgb565>,
{
    Line::new(a, b)
        .into_styled(PrimitiveStyle::with_stroke(color, width))
        .draw(target)
}
