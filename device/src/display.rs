//! SSD1351 driver for the Waveshare 1.5" 128x128 RGB OLED (SPI, RGB565).
//!
//! This is a small, self-contained driver tuned for Hologotchi's constraints:
//!
//! * One fixed-size 128x128 RGB565 framebuffer (32 KB) parked in `.bss` — no heap,
//!   no large stack temporaries, deterministic memory use.
//! * A single, explicit orientation/mirror path via the SSD1351 re-map register
//!   (`0xA0`). The panel is viewed through a dichroic cube, so mirror/flip
//!   correction is a first-class setting rather than a side effect of a rotation
//!   preset — see [`Orientation`].
//! * Blocking SPI. Phase 2 only drives the display, so the simplest flush is the
//!   right one; this is the single place to swap in async/DMA later if Wi-Fi plus
//!   rendering ever proves too tight.
//!
//! Drawing goes through [`embedded_graphics`]: the driver is a
//! [`DrawTarget`] writing into the framebuffer, and [`Ssd1351::flush`] ships the
//! whole buffer to the panel.

use core::cell::UnsafeCell;
use core::convert::Infallible;
use core::sync::atomic::{AtomicBool, Ordering};

use embedded_graphics::Pixel;
use embedded_graphics::draw_target::DrawTarget;
use embedded_graphics::geometry::{OriginDimensions, Size};
use embedded_graphics::pixelcolor::{Rgb565, RgbColor};
use esp_hal::Blocking;
use esp_hal::delay::Delay;
use esp_hal::gpio::Output;
use esp_hal::spi::Error as SpiError;
use esp_hal::spi::master::Spi;

/// Panel width in pixels.
pub const WIDTH: usize = 128;
/// Panel height in pixels.
pub const HEIGHT: usize = 128;
/// Framebuffer length in bytes (RGB565 = 2 bytes per pixel).
pub const FB_LEN: usize = WIDTH * HEIGHT * 2;

// --- SSD1351 command set (subset we use) ---
const CMD_COMMAND_LOCK: u8 = 0xFD;
const CMD_DISPLAY_OFF: u8 = 0xAE;
const CMD_DISPLAY_ON: u8 = 0xAF;
const CMD_CLOCK_DIV: u8 = 0xB3;
const CMD_MUX_RATIO: u8 = 0xCA;
const CMD_SET_REMAP: u8 = 0xA0;
const CMD_START_LINE: u8 = 0xA1;
const CMD_DISPLAY_OFFSET: u8 = 0xA2;
const CMD_NORMAL_DISPLAY: u8 = 0xA6;
const CMD_SET_GPIO: u8 = 0xB5;
const CMD_FUNCTION_SELECT: u8 = 0xAB;
const CMD_SET_VSL: u8 = 0xB4;
const CMD_CONTRAST_ABC: u8 = 0xC1;
const CMD_CONTRAST_MASTER: u8 = 0xC7;
const CMD_PRECHARGE: u8 = 0xB1;
const CMD_PRECHARGE2: u8 = 0xB6;
const CMD_VCOMH: u8 = 0xBE;
const CMD_SET_COLUMN: u8 = 0x15;
const CMD_SET_ROW: u8 = 0x75;
const CMD_WRITE_RAM: u8 = 0x5C;

// --- Re-map register (0xA0) bit fields ---
/// bit6 = 1: 65k (16-bit) colour depth.
const REMAP_65K_COLOR: u8 = 0b0100_0000;
/// bit5 = 1: odd/even COM split — required by this 128-row panel.
const REMAP_COM_SPLIT: u8 = 0b0010_0000;
/// bit4 = 1: reverse COM scan direction (vertical flip).
const REMAP_FLIP_V: u8 = 0b0001_0000;
/// bit2 = 1: swap colour order to B-G-R.
const REMAP_BGR: u8 = 0b0000_0100;
/// bit1 = 1: reverse column address (horizontal mirror).
const REMAP_MIRROR_H: u8 = 0b0000_0010;
/// bit0 = 1: vertical address increment.
const REMAP_VERTICAL_INCREMENT: u8 = 0b0000_0001;

/// How the framebuffer maps onto the physical panel.
///
/// Because the display is viewed through a dichroic cube, the "correct" picture
/// usually needs one mirrored axis relative to a direct-view panel. Keep this as
/// the single orientation knob so the corrected path lives in exactly one place.
#[derive(Clone, Copy)]
pub struct Orientation {
    /// Write RAM top-to-bottom before advancing to the next column.
    pub vertical_increment: bool,
    /// Mirror horizontally (reverse column address).
    pub mirror_h: bool,
    /// Flip vertically (reverse COM scan direction).
    pub flip_v: bool,
    /// Use B-G-R colour order instead of R-G-B.
    pub bgr: bool,
}

impl Orientation {
    /// Direct-view panel orientation (no cube). Matches the common Waveshare/
    /// Adafruit re-map value `0x74`.
    pub const PANEL: Self = Self {
        vertical_increment: false,
        mirror_h: false,
        flip_v: true,
        bgr: true,
    };

    /// Orientation corrected for viewing through the dichroic cube: the cube
    /// reflects the image, so mirror one axis relative to [`Orientation::PANEL`].
    /// Tune these flags during the hardware "cube orientation" smoke test.
    pub const CUBE: Self = Self {
        vertical_increment: false,
        mirror_h: true,
        flip_v: true,
        bgr: true,
    };

    /// Cube-corrected orientation for a panel mounted sideways, rotated 90
    /// degrees clockwise relative to [`Orientation::CUBE`].
    pub const CUBE_ROTATED_CW: Self = Self {
        vertical_increment: true,
        mirror_h: false,
        flip_v: true,
        bgr: true,
    };

    /// Build the SSD1351 re-map byte (command `0xA0`) for this orientation.
    fn remap_byte(self) -> u8 {
        let mut b = REMAP_65K_COLOR | REMAP_COM_SPLIT;
        if self.vertical_increment {
            b |= REMAP_VERTICAL_INCREMENT;
        }
        if self.mirror_h {
            b |= REMAP_MIRROR_H;
        }
        if self.flip_v {
            b |= REMAP_FLIP_V;
        }
        if self.bgr {
            b |= REMAP_BGR;
        }
        b
    }
}

/// `.bss`-resident framebuffer storage. Zero-initialised at load, so there is no
/// 32 KB stack temporary and no heap allocation.
struct FbStorage(UnsafeCell<[u8; FB_LEN]>);
// SAFETY: exactly one `&mut` is ever handed out, guarded by `FB_TAKEN`.
unsafe impl Sync for FbStorage {}

static FB: FbStorage = FbStorage(UnsafeCell::new([0; FB_LEN]));
static FB_TAKEN: AtomicBool = AtomicBool::new(false);

/// Hand out the one-and-only mutable reference to the static framebuffer.
fn take_framebuffer() -> &'static mut [u8; FB_LEN] {
    // riscv32imc has no atomic read-modify-write; plain load/store is enough
    // because this only runs once during single-threaded start-up.
    assert!(
        !FB_TAKEN.load(Ordering::SeqCst),
        "framebuffer already taken"
    );
    FB_TAKEN.store(true, Ordering::SeqCst);
    // SAFETY: the guard above guarantees this runs at most once, so no other
    // reference to `FB` exists.
    unsafe { &mut *FB.0.get() }
}

/// SSD1351 OLED over blocking SPI with a single back buffer.
pub struct Ssd1351 {
    spi: Spi<'static, Blocking>,
    dc: Output<'static>,
    cs: Output<'static>,
    rst: Output<'static>,
    fb: &'static mut [u8; FB_LEN],
}

impl Ssd1351 {
    /// Create the driver, claiming the static framebuffer.
    ///
    /// Panics if called more than once (only one display is supported).
    pub fn new(
        spi: Spi<'static, Blocking>,
        dc: Output<'static>,
        cs: Output<'static>,
        rst: Output<'static>,
    ) -> Self {
        Self {
            spi,
            dc,
            cs,
            rst,
            fb: take_framebuffer(),
        }
    }

    /// Pulse the hardware reset line.
    fn reset(&mut self) {
        let delay = Delay::new();
        self.rst.set_high();
        delay.delay_millis(1);
        self.rst.set_low();
        delay.delay_millis(10);
        self.rst.set_high();
        delay.delay_millis(10);
    }

    /// Send a command byte followed by optional data bytes, framed by CS.
    fn cmd(&mut self, command: u8, args: &[u8]) -> Result<(), SpiError> {
        self.cs.set_low();
        self.dc.set_low();
        self.spi.write(&[command])?;
        if !args.is_empty() {
            self.dc.set_high();
            self.spi.write(args)?;
        }
        self.cs.set_high();
        Ok(())
    }

    /// Reset and initialise the panel, then clear it to black and turn it on.
    pub fn init(&mut self, orientation: Orientation) -> Result<(), SpiError> {
        self.reset();

        self.cmd(CMD_COMMAND_LOCK, &[0x12])?; // unlock MCU interface
        self.cmd(CMD_COMMAND_LOCK, &[0xB1])?; // unlock advanced commands
        self.cmd(CMD_DISPLAY_OFF, &[])?;
        self.cmd(CMD_CLOCK_DIV, &[0xF1])?; // osc freq + clock divider
        self.cmd(CMD_MUX_RATIO, &[0x7F])?; // 128 rows (mux ratio 127)
        self.cmd(CMD_DISPLAY_OFFSET, &[0x00])?;
        self.cmd(CMD_START_LINE, &[0x00])?;
        self.cmd(CMD_SET_GPIO, &[0x00])?;
        self.cmd(CMD_FUNCTION_SELECT, &[0x01])?; // internal Vdd regulator
        self.cmd(CMD_SET_VSL, &[0xA0, 0xB5, 0x55])?; // external VSL
        self.cmd(CMD_CONTRAST_ABC, &[0xC8, 0x80, 0xC8])?;
        self.cmd(CMD_CONTRAST_MASTER, &[0x0F])?;
        self.cmd(CMD_PRECHARGE, &[0x32])?;
        self.cmd(CMD_PRECHARGE2, &[0x01])?;
        self.cmd(CMD_VCOMH, &[0x05])?;
        self.cmd(CMD_NORMAL_DISPLAY, &[])?;
        self.cmd(CMD_SET_REMAP, &[orientation.remap_byte()])?;

        // Clear the panel RAM (which powers up with noise) while the display is
        // still off, so the first thing the user sees is a clean frame.
        self.fb.fill(0);
        self.flush()?;

        self.cmd(CMD_DISPLAY_ON, &[])?;
        Ok(())
    }

    /// Address the full panel for the next RAM write.
    fn set_full_window(&mut self) -> Result<(), SpiError> {
        self.cmd(CMD_SET_COLUMN, &[0x00, (WIDTH - 1) as u8])?;
        self.cmd(CMD_SET_ROW, &[0x00, (HEIGHT - 1) as u8])?;
        Ok(())
    }

    /// Ship the whole framebuffer to the panel.
    pub fn flush(&mut self) -> Result<(), SpiError> {
        self.set_full_window()?;
        self.cs.set_low();
        self.dc.set_low();
        self.spi.write(&[CMD_WRITE_RAM])?;
        self.dc.set_high();
        self.spi.write(&self.fb[..])?;
        self.cs.set_high();
        Ok(())
    }
}

/// Pack an `Rgb565` colour into the panel's big-endian 16-bit word.
#[inline]
fn rgb565_bytes(color: Rgb565) -> [u8; 2] {
    let word = (color.r() as u16) << 11 | (color.g() as u16) << 5 | color.b() as u16;
    [(word >> 8) as u8, word as u8]
}

impl OriginDimensions for Ssd1351 {
    fn size(&self) -> Size {
        Size::new(WIDTH as u32, HEIGHT as u32)
    }
}

impl DrawTarget for Ssd1351 {
    type Color = Rgb565;
    type Error = Infallible;

    fn draw_iter<I>(&mut self, pixels: I) -> Result<(), Self::Error>
    where
        I: IntoIterator<Item = Pixel<Self::Color>>,
    {
        for Pixel(coord, color) in pixels {
            let (x, y) = (coord.x, coord.y);
            if x < 0 || y < 0 || x as usize >= WIDTH || y as usize >= HEIGHT {
                continue;
            }
            let idx = (y as usize * WIDTH + x as usize) * 2;
            let bytes = rgb565_bytes(color);
            self.fb[idx] = bytes[0];
            self.fb[idx + 1] = bytes[1];
        }
        Ok(())
    }

    fn clear(&mut self, color: Self::Color) -> Result<(), Self::Error> {
        let [hi, lo] = rgb565_bytes(color);
        if hi == lo {
            self.fb.fill(hi);
        } else {
            for px in self.fb.chunks_exact_mut(2) {
                px[0] = hi;
                px[1] = lo;
            }
        }
        Ok(())
    }
}
