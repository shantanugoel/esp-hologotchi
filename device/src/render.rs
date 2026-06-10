//! Mochi's local idle animation.
//!
//! Phase 2 implements the single idle behaviour from `PET.md`: a calm, breathing
//! Shiba face that blinks now and then so it still feels alive with no host
//! attached. Everything here is integer / fixed-point — there are no floats, and
//! motion comes from a small sine lookup table, which keeps the renderer cheap
//! and deterministic on the FPU-less ESP32-C3.

use embedded_graphics::pixelcolor::Rgb565;
use embedded_graphics::prelude::*;
use embedded_graphics::primitives::{Circle, Line, PrimitiveStyle, Triangle};

/// `sin(2*pi * i / 64) * 1024`, integer-only motion source.
const SIN: [i16; 64] = [
    0, 100, 200, 297, 392, 483, 569, 650, 724, 792, 851, 903, 946, 980, 1004, 1019, 1024, 1019,
    1004, 980, 946, 903, 851, 792, 724, 650, 569, 483, 392, 297, 200, 100, 0, -100, -200, -297,
    -392, -483, -569, -650, -724, -792, -851, -903, -946, -980, -1004, -1019, -1024, -1019, -1004,
    -980, -946, -903, -851, -792, -724, -650, -569, -483, -392, -297, -200, -100,
];

/// Peak vertical bob of the breathing loop, in pixels.
const BREATH_AMPL: i32 = 3;
/// Peak horizontal sway, in pixels.
const SWAY_AMPL: i32 = 1;
/// Frames between blinks (~5.5 s at 20 fps).
const BLINK_PERIOD: u32 = 110;
/// How many frames an eye stays shut (~0.2 s at 20 fps).
const BLINK_LEN: u32 = 4;

// Mochi's palette. Bright, warm fur on a black field reads well as a hologram.
const ORANGE: Rgb565 = Rgb565::new(28, 30, 5);
const CREAM: Rgb565 = Rgb565::new(30, 57, 25);
const INK: Rgb565 = Rgb565::new(2, 3, 1);
const SHINE: Rgb565 = Rgb565::new(31, 63, 31);

/// Mochi's idle scene state. Just a frame counter for now.
pub struct Scene {
    frame: u32,
}

impl Default for Scene {
    fn default() -> Self {
        Self::new()
    }
}

impl Scene {
    /// Create a fresh idle scene.
    pub fn new() -> Self {
        Self { frame: 0 }
    }

    /// Advance the animation by one frame.
    pub fn tick(&mut self) {
        self.frame = self.frame.wrapping_add(1);
    }

    /// Render the current frame into `target`.
    pub fn draw<D>(&self, target: &mut D) -> Result<(), D::Error>
    where
        D: DrawTarget<Color = Rgb565>,
    {
        target.clear(Rgb565::BLACK)?;

        let idx = (self.frame % 64) as usize;
        let oy = (SIN[idx] as i32 * BREATH_AMPL) / 1024;
        let ox = (SIN[(idx + 16) % 64] as i32 * SWAY_AMPL) / 1024;
        let blinking = self.frame % BLINK_PERIOD < BLINK_LEN;

        // Ears (drawn first so the head overlaps their base).
        fill_triangle(
            target,
            pt(30, 44, ox, oy),
            pt(44, 8, ox, oy),
            pt(62, 40, ox, oy),
            ORANGE,
        )?;
        fill_triangle(
            target,
            pt(98, 44, ox, oy),
            pt(84, 8, ox, oy),
            pt(66, 40, ox, oy),
            ORANGE,
        )?;
        fill_triangle(
            target,
            pt(40, 38, ox, oy),
            pt(46, 18, ox, oy),
            pt(56, 36, ox, oy),
            CREAM,
        )?;
        fill_triangle(
            target,
            pt(88, 38, ox, oy),
            pt(82, 18, ox, oy),
            pt(72, 36, ox, oy),
            CREAM,
        )?;

        // Head, then the cream lower-face mask.
        fill_circle(target, 64 + ox, 60 + oy, 84, ORANGE)?;
        fill_circle(target, 64 + ox, 78 + oy, 64, CREAM)?;

        // Shiba eyebrow dots on the orange forehead.
        fill_circle(target, 46 + ox, 44 + oy, 9, CREAM)?;
        fill_circle(target, 82 + ox, 44 + oy, 9, CREAM)?;

        // Eyes: open with a catchlight, or a shut eyelid line while blinking.
        if blinking {
            stroke_line(target, pt(44, 58, ox, oy), pt(56, 58, ox, oy), 3, INK)?;
            stroke_line(target, pt(72, 58, ox, oy), pt(84, 58, ox, oy), 3, INK)?;
        } else {
            fill_circle(target, 50 + ox, 58 + oy, 13, INK)?;
            fill_circle(target, 78 + ox, 58 + oy, 13, INK)?;
            fill_circle(target, 48 + ox, 56 + oy, 4, SHINE)?;
            fill_circle(target, 76 + ox, 56 + oy, 4, SHINE)?;
        }

        // Nose and the little smile.
        fill_triangle(
            target,
            pt(57, 70, ox, oy),
            pt(71, 70, ox, oy),
            pt(64, 82, ox, oy),
            INK,
        )?;
        stroke_line(target, pt(64, 82, ox, oy), pt(54, 90, ox, oy), 2, INK)?;
        stroke_line(target, pt(64, 82, ox, oy), pt(74, 90, ox, oy), 2, INK)?;

        Ok(())
    }
}

/// Offset a base coordinate by the current breathing/sway amount.
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
