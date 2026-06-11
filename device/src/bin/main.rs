#![no_std]
#![no_main]
#![deny(
    clippy::mem_forget,
    reason = "mem::forget is generally not safe to do with esp_hal types, especially those \
    holding buffers for the duration of a data transfer."
)]
#![deny(clippy::large_stack_frames)]

//! Phase 2 firmware entry point: bring up the SSD1351 OLED and run Mochi's local
//! idle animation. Wi-Fi and the host brain come in later phases.

use embassy_executor::Spawner;
use embassy_time::{Duration, Ticker, Timer};
use esp_backtrace as _;
use esp_hal::clock::CpuClock;
use esp_hal::gpio::{Level, Output, OutputConfig};
use esp_hal::interrupt::software::SoftwareInterruptControl;
use esp_hal::spi::Mode;
use esp_hal::spi::master::{Config as SpiConfig, Spi};
use esp_hal::time::Rate;
use esp_hal::timer::timg::TimerGroup;
use esp_hologotchi::display::{Orientation, Ssd1351};
use esp_hologotchi::render::Scene;
use log::{info, warn};

extern crate alloc;

// This creates a default app-descriptor required by the esp-idf bootloader.
esp_bootloader_esp_idf::esp_app_desc!();

/// Render tick. 20 fps is plenty for the calm idle loop and leaves the blocking
/// SPI flush comfortable headroom within each frame.
const FRAME_MS: u64 = 50;

#[allow(
    clippy::large_stack_frames,
    reason = "it's not unusual to allocate larger buffers etc. in main"
)]
#[esp_rtos::main]
async fn main(spawner: Spawner) -> ! {
    esp_println::logger::init_logger_from_env();

    let config = esp_hal::Config::default().with_cpu_clock(CpuClock::max());
    let peripherals = esp_hal::init(config);

    esp_alloc::heap_allocator!(#[esp_hal::ram(reclaimed)] size: 66320);

    let timg0 = TimerGroup::new(peripherals.TIMG0);
    let sw_interrupt = SoftwareInterruptControl::new(peripherals.SW_INTERRUPT);
    esp_rtos::start(timg0.timer0, sw_interrupt.software_interrupt0);

    info!("Embassy initialized!");

    // --- SSD1351 OLED over blocking SPI ---
    // Pin map matching the current hardware wiring:
    // CLK=GPIO4, MOSI(DIN)=GPIO6, CS=GPIO7, DC=GPIO3, RST=GPIO2.
    let spi = Spi::new(
        peripherals.SPI2,
        SpiConfig::default()
            .with_frequency(Rate::from_mhz(16))
            .with_mode(Mode::_0),
    )
    .expect("Failed to initialize SPI")
    .with_sck(peripherals.GPIO4)
    .with_mosi(peripherals.GPIO6);

    let dc = Output::new(peripherals.GPIO3, Level::Low, OutputConfig::default());
    let cs = Output::new(peripherals.GPIO7, Level::High, OutputConfig::default());
    let rst = Output::new(peripherals.GPIO2, Level::High, OutputConfig::default());

    let mut display = Ssd1351::new(spi, dc, cs, rst);
    if let Err(e) = display.init(Orientation::CUBE) {
        panic!(
            "SSD1351 init failed on SPI2 (CLK=GPIO4/MOSI=GPIO6/CS=GPIO7/DC=GPIO3/RST=GPIO2): \
             {:?}. Check wiring and power.",
            e
        );
    }
    info!("SSD1351 initialized");
    spawner.spawn(render_task(display).unwrap());

    info!("esp-hologotchi started");
    loop {
        Timer::after(Duration::from_secs(3600)).await;
    }
}

/// Fixed-rate idle render loop: advance the scene, draw it into the back buffer,
/// then flush the whole frame to the panel.
#[embassy_executor::task]
#[allow(
    clippy::large_stack_frames,
    reason = "the styled embedded-graphics primitives briefly exceed the strict 1KB threshold"
)]
async fn render_task(mut display: Ssd1351) {
    let mut scene = Scene::new();
    let mut ticker = Ticker::every(Duration::from_millis(FRAME_MS));
    loop {
        scene.tick();
        let _ = scene.draw(&mut display);
        if let Err(e) = display.flush() {
            warn!("display flush failed: {:?}", e);
        }
        ticker.next().await;
    }
}
