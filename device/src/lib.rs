#![no_std]
//! Hologotchi device firmware (ESP32-C3).
//!
//! Phase 2 brings up the display: an SSD1351 128x128 RGB OLED driven over SPI
//! ([`display`]) showing Mochi's local idle animation ([`render`]). The host
//! brain and Wi-Fi transport arrive in later phases; the device already feels
//! alive on its own.

pub mod display;
pub mod render;
