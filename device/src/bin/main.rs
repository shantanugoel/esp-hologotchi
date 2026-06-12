#![no_std]
#![no_main]
#![deny(
    clippy::mem_forget,
    reason = "mem::forget is generally not safe to do with esp_hal types, especially those \
    holding buffers for the duration of a data transfer."
)]
#![deny(clippy::large_stack_frames)]

//! Phase 4 firmware entry point: keep Mochi idling on the OLED, connect the
//! ESP32-C3 to local Wi-Fi, and accept newline-delimited JSON behavior updates
//! over a small TCP control socket.

use core::cell::RefCell;

use critical_section::Mutex;
use embassy_executor::Spawner;
use embassy_net::tcp::TcpSocket;
use embassy_net::{Config as NetConfig, Runner, Stack, StackResources};
use embassy_time::{Duration, Ticker, Timer};
use esp_backtrace as _;
use esp_hal::clock::CpuClock;
use esp_hal::gpio::{Level, Output, OutputConfig};
use esp_hal::interrupt::software::SoftwareInterruptControl;
use esp_hal::rng::Rng;
use esp_hal::spi::Mode;
use esp_hal::spi::master::{Config as SpiConfig, Spi};
use esp_hal::time::Rate;
use esp_hal::timer::timg::TimerGroup;
use esp_hologotchi::behavior::{self, BehaviorUpdate};
use esp_hologotchi::display::{Orientation, Ssd1351};
use esp_hologotchi::render::Scene;
use esp_radio::wifi::sta::StationConfig;
use esp_radio::wifi::{
    self, AuthenticationMethod, Config as WifiConfig, ControllerConfig, WifiController,
};
use log::{info, warn};
use static_cell::StaticCell;

extern crate alloc;

// This creates a default app-descriptor required by the esp-idf bootloader.
esp_bootloader_esp_idf::esp_app_desc!();

/// Render tick. 20 fps is plenty for Mochi and gives the control loop clear,
/// deterministic timing.
const FRAME_MS: u32 = 50;
/// Default Wi-Fi control socket port shared with the host service.
const DEFAULT_CONTROL_PORT: u16 = 4242;
const SOCKET_BUF_LEN: usize = 256;
const READ_BUF_LEN: usize = 64;
const NET_STACK_SOCKETS: usize = 4;
const WIFI_RETRY_SECS: u64 = 3;
const SOCKET_TIMEOUT_SECS: u64 = 30;

type WifiDevice = wifi::Interface<'static>;
type NetRunner = Runner<'static, WifiDevice>;

static STACK_RESOURCES: StaticCell<StackResources<NET_STACK_SOCKETS>> = StaticCell::new();
static PENDING_BEHAVIOR: Mutex<RefCell<Option<BehaviorUpdate>>> = Mutex::new(RefCell::new(None));
static SOCKET_RX_BUF: StaticCell<[u8; SOCKET_BUF_LEN]> = StaticCell::new();
static SOCKET_TX_BUF: StaticCell<[u8; SOCKET_BUF_LEN]> = StaticCell::new();
static FRAME_BUF: StaticCell<[u8; behavior::FRAME_CAPACITY]> = StaticCell::new();
static READ_BUF: StaticCell<[u8; READ_BUF_LEN]> = StaticCell::new();

struct WifiCredentials {
    ssid: &'static str,
    password: &'static str,
}

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

    info!("embassy initialized");

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
    if let Err(e) = display.init(Orientation::CUBE_ROTATED_CW) {
        panic!(
            "SSD1351 init failed on SPI2 (CLK=GPIO4/MOSI=GPIO6/CS=GPIO7/DC=GPIO3/RST=GPIO2): \
             {:?}. Check wiring and power.",
            e
        );
    }
    info!("display initialized");
    spawner.spawn(render_task(display).unwrap());

    let control_port = control_port();

    if let Some(credentials) = wifi_credentials() {
        let station_config = build_station_config(&credentials);
        let controller_config =
            ControllerConfig::default().with_initial_config(WifiConfig::Station(station_config));
        let (controller, interfaces) = wifi::new(peripherals.WIFI, controller_config)
            .expect("failed to initialize Wi-Fi controller");

        let rng = Rng::new();
        let seed = ((rng.random() as u64) << 32) | (rng.random() as u64);
        let (stack, runner) = embassy_net::new(
            interfaces.station,
            NetConfig::dhcpv4(Default::default()),
            STACK_RESOURCES.init(StackResources::new()),
            seed,
        );

        spawner.spawn(net_task(runner).unwrap());
        spawner.spawn(behavior_server_task(stack, control_port).unwrap());

        info!(
            "Wi-Fi control enabled for SSID '{}'; listening on TCP {} once DHCP completes",
            credentials.ssid, control_port
        );
        info!("esp-hologotchi started");
        wifi_connection_loop(controller).await;
        unreachable!("Wi-Fi connection loop never returns");
    } else {
        warn!(
            "device/hologotchi.toml does not provide a Wi-Fi SSID; running local idle only. \
             Copy device/hologotchi.example.toml to device/hologotchi.toml and fill in your \
             network details to enable Wi-Fi control."
        );
    }

    info!("esp-hologotchi started");
    loop {
        Timer::after(Duration::from_secs(3600)).await;
    }
}

/// Fixed-rate render loop: apply the latest host behavior, draw Mochi's current
/// frame, flush it to the OLED, then advance to the next frame.
#[embassy_executor::task]
#[allow(
    clippy::large_stack_frames,
    reason = "the styled embedded-graphics primitives briefly exceed the strict 1KB threshold"
)]
async fn render_task(mut display: Ssd1351) {
    let mut scene = Scene::new();
    let mut ticker = Ticker::every(Duration::from_millis(FRAME_MS as u64));

    loop {
        if let Some(update) = take_behavior_update() {
            scene.apply_behavior(update, FRAME_MS);
        }
        let _ = scene.draw(&mut display);
        if let Err(e) = display.flush() {
            warn!("display flush failed: {:?}", e);
        }
        scene.tick();
        ticker.next().await;
    }
}

#[embassy_executor::task]
async fn net_task(mut runner: NetRunner) -> ! {
    runner.run().await
}

#[allow(
    clippy::large_stack_frames,
    reason = "esp-radio keeps substantial controller state in WifiController; the loop owns exactly \
    one bounded controller instance for the life of the firmware"
)]
async fn wifi_connection_loop(mut controller: WifiController<'static>) {
    loop {
        if controller.is_connected() {
            match controller.wait_for_disconnect_async().await {
                Ok(info) => {
                    warn!("Wi-Fi disconnected: {:?}", info);
                }
                Err(err) => {
                    warn!("Wi-Fi disconnect wait failed: {:?}", err);
                }
            }
        } else {
            match controller.connect_async().await {
                Ok(info) => {
                    info!("Wi-Fi connected: {:?}", info);
                    continue;
                }
                Err(err) => {
                    warn!("Wi-Fi connect failed: {:?}", err);
                }
            }
        }

        Timer::after(Duration::from_secs(WIFI_RETRY_SECS)).await;
    }
}

#[embassy_executor::task]
#[allow(
    clippy::large_stack_frames,
    reason = "the task owns one bounded TCP socket plus fixed-size control buffers parked in \
    StaticCell; Clippy still counts the initialization site pessimistically"
)]
async fn behavior_server_task(stack: Stack<'static>, control_port: u16) {
    let rx_buffer = SOCKET_RX_BUF.init([0; SOCKET_BUF_LEN]);
    let tx_buffer = SOCKET_TX_BUF.init([0; SOCKET_BUF_LEN]);
    let frame = FRAME_BUF.init([0; behavior::FRAME_CAPACITY]);
    let read_buf = READ_BUF.init([0; READ_BUF_LEN]);

    loop {
        stack.wait_config_up().await;
        if let Some(config) = stack.config_v4() {
            info!(
                "control socket listening on {:?}:{}",
                config.address.address(),
                control_port
            );
        }

        let mut socket = TcpSocket::new(stack, rx_buffer, tx_buffer);
        socket.set_timeout(Some(Duration::from_secs(SOCKET_TIMEOUT_SECS)));

        match socket.accept(control_port).await {
            Ok(()) => {
                info!("host control connection accepted");
                if let Err(err) = read_control_stream(&mut socket, frame, read_buf).await {
                    warn!("control connection ended with error: {:?}", err);
                } else {
                    info!("host control connection closed");
                }
            }
            Err(err) => {
                warn!("control accept failed: {:?}", err);
                Timer::after(Duration::from_millis(250)).await;
            }
        }
    }
}

async fn read_control_stream(
    socket: &mut TcpSocket<'_>,
    frame: &mut [u8; behavior::FRAME_CAPACITY],
    read_buf: &mut [u8; READ_BUF_LEN],
) -> Result<(), embassy_net::tcp::Error> {
    let mut frame_len = 0usize;
    let mut dropping_line = false;

    loop {
        let count = socket.read(read_buf).await?;
        if count == 0 {
            return Ok(());
        }

        for &byte in &read_buf[..count] {
            if byte == b'\r' {
                continue;
            }
            if byte == b'\n' {
                if dropping_line {
                    warn!("dropping overlong behavior frame");
                } else {
                    handle_frame(&frame[..frame_len]);
                }
                frame_len = 0;
                dropping_line = false;
                continue;
            }

            if dropping_line {
                continue;
            }

            if frame_len < frame.len() {
                frame[frame_len] = byte;
                frame_len += 1;
            } else {
                dropping_line = true;
            }
        }
    }
}

fn handle_frame(frame: &[u8]) {
    match behavior::parse(frame) {
        Ok(update) => {
            info!("accepted behavior update: {:?}", update);
            queue_behavior_update(update);
        }
        Err(behavior::ParseError::Empty) => {}
        Err(err) => {
            warn!("ignored malformed behavior frame: {:?}", err);
        }
    }
}

fn queue_behavior_update(update: BehaviorUpdate) {
    critical_section::with(|cs| {
        PENDING_BEHAVIOR.borrow(cs).borrow_mut().replace(update);
    });
}

fn take_behavior_update() -> Option<BehaviorUpdate> {
    critical_section::with(|cs| PENDING_BEHAVIOR.borrow(cs).borrow_mut().take())
}

fn wifi_credentials() -> Option<WifiCredentials> {
    let ssid = option_env!("HOLOGOTCHI_WIFI_SSID")?;
    let ssid = ssid.trim();
    if ssid.is_empty() {
        return None;
    }

    Some(WifiCredentials {
        ssid,
        password: option_env!("HOLOGOTCHI_WIFI_PASSWORD").map_or("", str::trim),
    })
}

fn control_port() -> u16 {
    option_env!("HOLOGOTCHI_CONTROL_PORT")
        .and_then(|raw| raw.parse::<u16>().ok())
        .filter(|port| *port != 0)
        .unwrap_or(DEFAULT_CONTROL_PORT)
}

fn build_station_config(credentials: &WifiCredentials) -> StationConfig {
    let mut station = StationConfig::default()
        .with_ssid(credentials.ssid)
        .with_password(credentials.password.into());
    if credentials.password.is_empty() {
        station = station.with_auth_method(AuthenticationMethod::None);
    }
    station
}
