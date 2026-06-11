//! Mochi's local idle animation.
//!
//! Phase 2 implements the single idle behaviour from `PET.md`: a calm, breathing
//! Shiba that blinks now and then so it still feels alive with no host attached.
//! Mochi is drawn as a full sitting Shiba Inu — red-gold fur, cream chest and
//! paws, soft inner ears, a curled tail, and a happy face — so the silhouette
//! reads instantly through the cube. Everything here is integer /
//! fixed-point: there are no floats, and motion comes from a small sine lookup
//! table, which keeps the renderer cheap and deterministic on the FPU-less
//! ESP32-C3.

use embedded_graphics::pixelcolor::Rgb565;
use embedded_graphics::prelude::*;
use embedded_graphics::primitives::{
    Circle, Ellipse, Line, PrimitiveStyle, Rectangle, RoundedRectangle, Triangle,
};

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

// Mochi's palette aims for a more natural red Shiba coat while keeping enough
// contrast to read clearly against the black hologram background. Navy is only
// ever drawn on top of a lighter fill (eyes, nose, toe lines), so it never
// disappears into the black background.
const ORANGE: Rgb565 = Rgb565::new(25, 31, 7);
const ORANGE_DK: Rgb565 = Rgb565::new(19, 22, 4);
const CREAM: Rgb565 = Rgb565::new(29, 56, 23);
const PINK: Rgb565 = Rgb565::new(27, 43, 18);
const BLUSH: Rgb565 = Rgb565::new(29, 39, 17);
const NAVY: Rgb565 = Rgb565::new(3, 7, 12);
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

        // --- Body (drawn first, the head sits on top of it) ---

        // Curled tail peeking out on Mochi's left (viewer's right).
        fill_circle(target, 103 + ox, 88 + oy, 30, ORANGE)?;
        stroke_circle(target, 105 + ox, 90 + oy, 15, ORANGE_DK, 3)?;
        fill_circle(target, 105 + ox, 90 + oy, 6, CREAM)?;

        // Seated haunches.
        fill_circle(target, 64 + ox, 100 + oy, 76, ORANGE)?;

        // White chest/belly flowing down into the front paws.
        fill_ellipse(target, 64 + ox, 102 + oy, 48, 54, CREAM)?;
        fill_round_rect(target, 45 + ox, 96 + oy, 17, 34, 8, CREAM)?;
        fill_round_rect(target, 66 + ox, 96 + oy, 17, 34, 8, CREAM)?;
        // Gap + toe creases so the two front paws read as paws.
        stroke_line(
            target,
            pt(64, 112, ox, oy),
            pt(64, 128, ox, oy),
            2,
            ORANGE_DK,
        )?;
        stroke_line(
            target,
            pt(50, 122, ox, oy),
            pt(50, 128, ox, oy),
            2,
            ORANGE_DK,
        )?;
        stroke_line(
            target,
            pt(57, 122, ox, oy),
            pt(57, 128, ox, oy),
            2,
            ORANGE_DK,
        )?;
        stroke_line(
            target,
            pt(72, 122, ox, oy),
            pt(72, 128, ox, oy),
            2,
            ORANGE_DK,
        )?;
        stroke_line(
            target,
            pt(79, 122, ox, oy),
            pt(79, 128, ox, oy),
            2,
            ORANGE_DK,
        )?;

        // --- Head ---

        // Ears: draw the outer fur first so the head can overlap the base and
        // keep the silhouette tucked in. The triangles are also shorter and a
        // bit broader so they read as softer, cuter Shiba ears.
        fill_triangle(
            target,
            pt(57, 31, ox, oy),
            pt(41, 9, ox, oy),
            pt(35, 37, ox, oy),
            ORANGE,
        )?;
        fill_triangle(
            target,
            pt(71, 31, ox, oy),
            pt(87, 9, ox, oy),
            pt(93, 37, ox, oy),
            ORANGE,
        )?;

        // Orange dome overlapping the ear bases.
        fill_circle(target, 64 + ox, 47 + oy, 66, ORANGE)?;

        // Inner ear triangles stay above the head so the soft peach tone
        // remains visible without letting the base poke out of the silhouette.
        fill_triangle(
            target,
            pt(53, 30, ox, oy),
            pt(46, 19, ox, oy),
            pt(44, 34, ox, oy),
            PINK,
        )?;
        fill_triangle(
            target,
            pt(75, 30, ox, oy),
            pt(82, 19, ox, oy),
            pt(84, 34, ox, oy),
            PINK,
        )?;

        // White muzzle, kept fairly central so the orange cheeks still bulge out
        // to the sides of the face.
        fill_ellipse(target, 64 + ox, 67 + oy, 50, 40, CREAM)?;

        // Shiba brow spots on the orange forehead.
        fill_circle(target, 50 + ox, 33 + oy, 7, CREAM)?;
        fill_circle(target, 78 + ox, 33 + oy, 7, CREAM)?;

        // Subtle warm cheek shading, low and to the sides.
        fill_ellipse(target, 46 + ox, 70 + oy, 12, 7, BLUSH)?;
        fill_ellipse(target, 82 + ox, 70 + oy, 12, 7, BLUSH)?;

        // Eyes: big and round with a catchlight, or a shut eyelid line on a blink.
        let (lx, rx, ey) = (52, 76, 52);
        if blinking {
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
        } else {
            fill_circle(target, lx + ox, ey + oy, 16, NAVY)?;
            fill_circle(target, rx + ox, ey + oy, 16, NAVY)?;
            fill_circle(target, lx - 3 + ox, ey - 4 + oy, 6, SHINE)?;
            fill_circle(target, rx - 3 + ox, ey - 4 + oy, 6, SHINE)?;
            fill_circle(target, lx + 4 + ox, ey + 4 + oy, 3, SHINE)?;
            fill_circle(target, rx + 4 + ox, ey + 4 + oy, 3, SHINE)?;
        }

        // Nose and a happy upturned mouth.
        fill_triangle(
            target,
            pt(58, 62, ox, oy),
            pt(70, 62, ox, oy),
            pt(64, 71, ox, oy),
            NAVY,
        )?;
        fill_circle(target, 61 + ox, 64 + oy, 3, SHINE)?;
        stroke_line(target, pt(64, 71, ox, oy), pt(64, 76, ox, oy), 2, NAVY)?;
        stroke_line(target, pt(64, 76, ox, oy), pt(57, 79, ox, oy), 2, NAVY)?;
        stroke_line(target, pt(57, 79, ox, oy), pt(53, 75, ox, oy), 2, NAVY)?;
        stroke_line(target, pt(64, 76, ox, oy), pt(71, 79, ox, oy), 2, NAVY)?;
        stroke_line(target, pt(71, 79, ox, oy), pt(75, 75, ox, oy), 2, NAVY)?;

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
