use std::fs;
use std::path::PathBuf;

use serde::Deserialize;

fn main() {
    linker_be_nice();
    // make sure linkall.x is the last linker script (otherwise might cause problems with flip-link)
    println!("cargo:rustc-link-arg=-Tlinkall.x");
    emit_local_device_config();
}

fn linker_be_nice() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() > 1 {
        let kind = &args[1];
        let what = &args[2];

        match kind.as_str() {
            "undefined-symbol" => match what.as_str() {
                what if what.starts_with("_defmt_") => {
                    eprintln!();
                    eprintln!(
                        "💡 `defmt` not found - make sure `defmt.x` is added as a linker script and you have included `use defmt_rtt as _;`"
                    );
                    eprintln!();
                }
                "_stack_start" => {
                    eprintln!();
                    eprintln!("💡 Is the linker script `linkall.x` missing?");
                    eprintln!();
                }
                what if what.starts_with("esp_rtos_") => {
                    eprintln!();
                    eprintln!(
                        "💡 `esp-radio` has no scheduler enabled. Make sure you have initialized `esp-rtos` or provided an external scheduler."
                    );
                    eprintln!();
                }
                "embedded_test_linker_file_not_added_to_rustflags" => {
                    eprintln!();
                    eprintln!(
                        "💡 `embedded-test` not found - make sure `embedded-test.x` is added as a linker script for tests"
                    );
                    eprintln!();
                }
                "free"
                | "malloc"
                | "calloc"
                | "get_free_internal_heap_size"
                | "malloc_internal"
                | "realloc_internal"
                | "calloc_internal"
                | "free_internal" => {
                    eprintln!();
                    eprintln!(
                        "💡 Did you forget the `esp-alloc` dependency or didn't enable the `compat` feature on it?"
                    );
                    eprintln!();
                }
                _ => (),
            },
            // we don't have anything helpful for "missing-lib" yet
            _ => {
                std::process::exit(1);
            }
        }

        std::process::exit(0);
    }

    println!(
        "cargo:rustc-link-arg=--error-handling-script={}",
        std::env::current_exe().unwrap().display()
    );
}

#[derive(Debug, Default, Deserialize)]
struct LocalConfig {
    wifi: Option<WifiConfig>,
    control: Option<ControlConfig>,
}

#[derive(Debug, Default, Deserialize)]
struct WifiConfig {
    ssid: Option<String>,
    password: Option<String>,
}

#[derive(Debug, Default, Deserialize)]
struct ControlConfig {
    port: Option<u16>,
}

fn emit_local_device_config() {
    let manifest_dir = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap());
    let config_path = manifest_dir.join("hologotchi.toml");
    println!("cargo:rerun-if-changed={}", config_path.display());

    if !config_path.exists() {
        return;
    }

    let raw = fs::read_to_string(&config_path).unwrap_or_else(|err| {
        panic!(
            "failed to read device config from {}: {err}",
            config_path.display()
        )
    });
    let config: LocalConfig = toml::from_str(&raw).unwrap_or_else(|err| {
        panic!(
            "failed to parse device config {} as TOML: {err}",
            config_path.display()
        )
    });

    if let Some(wifi) = config.wifi {
        if let Some(ssid) = wifi.ssid {
            let ssid = ssid.trim();
            if !ssid.is_empty() {
                println!("cargo:rustc-env=HOLOGOTCHI_WIFI_SSID={ssid}");
            }
        }
        if let Some(password) = wifi.password {
            println!(
                "cargo:rustc-env=HOLOGOTCHI_WIFI_PASSWORD={}",
                password.trim()
            );
        }
    }

    if let Some(control) = config.control
        && let Some(port) = control.port
    {
        assert!(port != 0, "control.port must be between 1 and 65535");
        println!("cargo:rustc-env=HOLOGOTCHI_CONTROL_PORT={port}");
    }
}
