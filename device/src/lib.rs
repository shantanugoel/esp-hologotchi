#![no_std]
//! Hologotchi device firmware (ESP32-C3).
//!
//! Phase 4 keeps Shiro alive locally on the OLED ([`display`] + [`render`]) and
//! adds the first Wi-Fi control path ([`behavior`]): newline-delimited JSON
//! behavior updates from the host can temporarily override the local idle loop.

pub mod behavior;
pub mod display;
pub mod render;
pub(crate) mod shiro_sprites;
