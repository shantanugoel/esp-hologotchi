//! Bounded Shiro behavior updates received from the host over Wi-Fi.
//!
//! The wire format is the Phase 3/4 newline-delimited JSON contract from
//! `PLAN.md`: one `{"v":1,"kind":"behavior",...}` object per line. Firmware
//! keeps parsing deterministic and cheap:
//!
//! * closed `mood` / `animation` vocabularies shared with `PET.md`
//! * fixed-size `heapless::String`s
//! * length clamping for optional text
//! * malformed or mismatched frames rejected without changing the active pose

use heapless::String;
use serde::Deserialize;

/// Maximum text the renderer will keep and attempt to draw.
pub const TEXT_CAPACITY: usize = 24;
/// Longest JSON line the control socket accepts before dropping it.
pub const FRAME_CAPACITY: usize = 256;
/// Host-side V1 behavior minimum duration.
pub const MIN_DURATION_MS: u32 = 1_000;
/// Host-side V1 behavior maximum duration.
pub const MAX_DURATION_MS: u32 = 15_000;

const KIND_CAPACITY: usize = 16;
const RAW_TEXT_CAPACITY: usize = 96;

/// Closed V1 mood vocabulary from `PET.md`.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum Mood {
    Calm,
    Curious,
    Happy,
    Sleepy,
    Worried,
    Alert,
}

/// Closed V1 animation vocabulary from `PET.md`.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum Animation {
    Idle,
    Blink,
    LookAround,
    Confused,
    Walk,
    Happy,
    Play,
    Excited,
    Sleepy,
    Nap,
    Worried,
    Alert,
}

/// A validated host behavior update ready for the renderer.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BehaviorUpdate {
    pub mood: Mood,
    pub animation: Animation,
    pub text: Option<String<TEXT_CAPACITY>>,
    pub alert: bool,
    pub duration_ms: u32,
}

#[derive(Debug, Deserialize)]
struct RawBehaviorUpdate {
    v: u8,
    kind: String<KIND_CAPACITY>,
    mood: Mood,
    animation: Animation,
    text: Option<String<RAW_TEXT_CAPACITY>>,
    alert: bool,
    duration_ms: u32,
}

/// Reasons a received control frame was rejected.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ParseError {
    Empty,
    Json,
    TrailingBytes,
    UnsupportedVersion,
    WrongKind,
    MismatchedMood,
    InvalidAlertFlag,
}

impl BehaviorUpdate {
    /// Number of renderer frames this behavior should stay active for.
    pub fn frames_for(&self, frame_ms: u32) -> u16 {
        let rounded = self.duration_ms.saturating_add(frame_ms.saturating_sub(1)) / frame_ms;
        rounded.clamp(1, u16::MAX as u32) as u16
    }
}

/// Parse a single trimmed JSON behavior line.
pub fn parse(frame: &[u8]) -> Result<BehaviorUpdate, ParseError> {
    let trimmed = trim_ascii(frame);
    if trimmed.is_empty() {
        return Err(ParseError::Empty);
    }

    let (raw, used) =
        serde_json_core::from_slice::<RawBehaviorUpdate>(trimmed).map_err(|_| ParseError::Json)?;
    if trim_ascii(&trimmed[used..]).is_empty().not() {
        return Err(ParseError::TrailingBytes);
    }
    if raw.v != 1 {
        return Err(ParseError::UnsupportedVersion);
    }
    if raw.kind.as_str() != "behavior" {
        return Err(ParseError::WrongKind);
    }
    if !mood_matches_animation(raw.mood, raw.animation) {
        return Err(ParseError::MismatchedMood);
    }
    if raw.alert != matches!(raw.animation, Animation::Alert) {
        return Err(ParseError::InvalidAlertFlag);
    }

    Ok(BehaviorUpdate {
        mood: raw.mood,
        animation: raw.animation,
        text: clamp_text(raw.text),
        alert: raw.alert,
        duration_ms: raw.duration_ms.clamp(MIN_DURATION_MS, MAX_DURATION_MS),
    })
}

#[inline]
pub fn idle_capable(animation: Animation) -> bool {
    matches!(
        animation,
        Animation::Idle | Animation::Blink | Animation::LookAround
    )
}

fn mood_matches_animation(mood: Mood, animation: Animation) -> bool {
    matches!(
        (mood, animation),
        (Mood::Calm, Animation::Idle)
            | (Mood::Calm, Animation::Blink)
            | (Mood::Curious, Animation::LookAround)
            | (Mood::Curious, Animation::Confused)
            | (Mood::Curious, Animation::Walk)
            | (Mood::Happy, Animation::Happy)
            | (Mood::Happy, Animation::Play)
            | (Mood::Happy, Animation::Excited)
            | (Mood::Sleepy, Animation::Sleepy)
            | (Mood::Sleepy, Animation::Nap)
            | (Mood::Worried, Animation::Worried)
            | (Mood::Alert, Animation::Alert)
    )
}

fn clamp_text(raw: Option<String<RAW_TEXT_CAPACITY>>) -> Option<String<TEXT_CAPACITY>> {
    let raw = raw?;
    let mut out = String::<TEXT_CAPACITY>::new();
    for ch in raw.as_str().trim().chars() {
        if out.push(ch).is_err() {
            break;
        }
    }
    if out.is_empty() { None } else { Some(out) }
}

fn trim_ascii(mut bytes: &[u8]) -> &[u8] {
    while let Some((first, rest)) = bytes.split_first() {
        if first.is_ascii_whitespace() {
            bytes = rest;
        } else {
            break;
        }
    }
    while let Some((last, rest)) = bytes.split_last() {
        if last.is_ascii_whitespace() {
            bytes = rest;
        } else {
            break;
        }
    }
    bytes
}

trait BoolExt {
    fn not(self) -> bool;
}

impl BoolExt for bool {
    #[inline]
    fn not(self) -> bool {
        !self
    }
}
