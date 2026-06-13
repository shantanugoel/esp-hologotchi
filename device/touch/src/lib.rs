//! Pure, deterministic touch-gesture classifier for Mochi's TTP223 input.
//!
//! This crate has **no** dependencies and no hardware coupling so the firmware
//! can drive it from an interrupt-free polling loop while the host can unit-test
//! the exact same logic. It turns a stream of debounced level samples into the
//! closed `tap` / `hold` / `doubletap` vocabulary shared with the host
//! (`host/inputs.py`) and the V2 uplink wire frame.
//!
//! Semantics (see `PLAN_v2.md` "Firmware Gesture Classifier"):
//!
//! * The raw input is debounced: a level must stay stable for [`DEBOUNCE_MS`]
//!   before an edge is accepted, so contact bounce never produces a gesture.
//! * A press held for at least [`HOLD_MS`] is a [`Gesture::Hold`]. It is reported
//!   exactly once, on release, carrying the *final* contact duration (matching
//!   the uplink example `{"gesture":"hold","duration_ms":960}`); crossing the
//!   threshold only commits the press to being a hold rather than a tap.
//! * A shorter press is a tap candidate. A tap is only emitted once the
//!   [`DOUBLETAP_WINDOW_MS`] inter-tap window expires with no second press.
//! * If a second press arrives inside that window, a [`Gesture::DoubleTap`] is
//!   emitted immediately and the press is drained without re-interpretation.
//! * Tap/doubletap bursts are rate-limited to at most one per
//!   [`MIN_EVENT_GAP_MS`]; a deliberate hold is never dropped.
//!
//! The classifier is time-driven: [`TouchClassifier::update`] must be called
//! periodically (e.g. every few milliseconds) with the current debounced sample
//! and a monotonic millisecond timestamp, even when the level has not changed,
//! so the tap/doubletap timeouts can fire.

#![cfg_attr(not(test), no_std)]

/// Stable-level time required before an edge is accepted (contact debounce).
pub const DEBOUNCE_MS: u64 = 40;
/// Minimum press length that is reported as a [`Gesture::Hold`].
pub const HOLD_MS: u64 = 700;
/// Inter-tap window: a second press starting within this of the first tap's
/// release is a [`Gesture::DoubleTap`]; otherwise the first press is a tap.
pub const DOUBLETAP_WINDOW_MS: u64 = 400;
/// Minimum spacing between emitted tap/doubletap events (≈ at most ~4/sec).
pub const MIN_EVENT_GAP_MS: u64 = 250;
/// Upper bound on a reported hold duration so a stuck pad cannot overflow the
/// wire frame; the host clamps again on its side.
pub const HOLD_DURATION_MAX_MS: u32 = 60_000;

/// A classified physical-contact gesture.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Gesture {
    /// A quick boop.
    Tap,
    /// A sustained press; `duration_ms` is the final contact time on release.
    Hold { duration_ms: u32 },
    /// Two taps inside the doubletap window: a play invite.
    DoubleTap,
}

#[derive(Clone, Copy, Eq, PartialEq)]
enum Edge {
    Down,
    Up,
}

#[derive(Clone, Copy)]
enum Phase {
    Idle,
    /// A press is in progress; `start_ms` is the debounced press time.
    FirstDown {
        start_ms: u64,
    },
    /// First tap released; waiting for a possible second tap until the window
    /// expires. `released_ms` is the debounced release time.
    WaitSecond {
        released_ms: u64,
    },
    /// A doubletap was already emitted on the second press; swallow its release.
    DrainRelease,
}

/// Deterministic tap/hold/doubletap state machine over debounced samples.
pub struct TouchClassifier {
    initialized: bool,
    raw_level: bool,
    raw_since_ms: u64,
    stable_level: bool,
    phase: Phase,
    last_emit_ms: u64,
    last_emit_valid: bool,
}

impl Default for TouchClassifier {
    fn default() -> Self {
        Self::new()
    }
}

impl TouchClassifier {
    /// Create an idle classifier. The first [`update`](Self::update) call seeds
    /// the debounce baseline from the observed level without emitting anything.
    pub const fn new() -> Self {
        Self {
            initialized: false,
            raw_level: false,
            raw_since_ms: 0,
            stable_level: false,
            phase: Phase::Idle,
            last_emit_ms: 0,
            last_emit_valid: false,
        }
    }

    /// Feed one sample. `pressed` is the raw (active-high) pad level and `now_ms`
    /// is a monotonic millisecond timestamp. Returns a gesture when one is
    /// recognized on this sample, otherwise `None`. At most one gesture is
    /// produced per call.
    pub fn update(&mut self, pressed: bool, now_ms: u64) -> Option<Gesture> {
        match self.debounce(pressed, now_ms) {
            Some(Edge::Down) => self.on_press(now_ms),
            Some(Edge::Up) => self.on_release(now_ms),
            None => self.on_tick(now_ms),
        }
    }

    fn debounce(&mut self, pressed: bool, now_ms: u64) -> Option<Edge> {
        if !self.initialized {
            self.initialized = true;
            self.raw_level = pressed;
            self.stable_level = pressed;
            self.raw_since_ms = now_ms;
            return None;
        }
        if pressed != self.raw_level {
            self.raw_level = pressed;
            self.raw_since_ms = now_ms;
        }
        if self.raw_level != self.stable_level
            && now_ms.saturating_sub(self.raw_since_ms) >= DEBOUNCE_MS
        {
            self.stable_level = self.raw_level;
            return Some(if self.stable_level {
                Edge::Down
            } else {
                Edge::Up
            });
        }
        None
    }

    fn on_press(&mut self, now_ms: u64) -> Option<Gesture> {
        match self.phase {
            Phase::Idle => {
                self.phase = Phase::FirstDown { start_ms: now_ms };
                None
            }
            Phase::WaitSecond { released_ms } => {
                if now_ms.saturating_sub(released_ms) < DOUBLETAP_WINDOW_MS {
                    self.phase = Phase::DrainRelease;
                    self.emit_limited(Gesture::DoubleTap, now_ms)
                } else {
                    // The window expired on the same sample this second-down edge
                    // was accepted, so `on_tick` never fired for the pending tap.
                    // Emit that first tap now, then begin a fresh press for this
                    // contact so it is never silently lost.
                    let pending_tap = self.emit_limited(Gesture::Tap, now_ms);
                    self.phase = Phase::FirstDown { start_ms: now_ms };
                    pending_tap
                }
            }
            // A down edge while already pressed should not happen with debounce.
            Phase::FirstDown { .. } | Phase::DrainRelease => None,
        }
    }

    fn on_release(&mut self, now_ms: u64) -> Option<Gesture> {
        match self.phase {
            Phase::FirstDown { start_ms } => {
                let duration = now_ms.saturating_sub(start_ms);
                if duration >= HOLD_MS {
                    self.phase = Phase::Idle;
                    // A hold is a deliberate gesture; deliver it unconditionally
                    // and reset the rate-limit baseline.
                    self.last_emit_ms = now_ms;
                    self.last_emit_valid = true;
                    Some(Gesture::Hold {
                        duration_ms: clamp_duration(duration),
                    })
                } else {
                    self.phase = Phase::WaitSecond {
                        released_ms: now_ms,
                    };
                    None
                }
            }
            Phase::DrainRelease => {
                self.phase = Phase::Idle;
                None
            }
            // Spurious release with nothing pressed.
            Phase::Idle | Phase::WaitSecond { .. } => None,
        }
    }

    fn on_tick(&mut self, now_ms: u64) -> Option<Gesture> {
        if let Phase::WaitSecond { released_ms } = self.phase
            && now_ms.saturating_sub(released_ms) >= DOUBLETAP_WINDOW_MS
        {
            self.phase = Phase::Idle;
            return self.emit_limited(Gesture::Tap, now_ms);
        }
        None
    }

    fn emit_limited(&mut self, gesture: Gesture, now_ms: u64) -> Option<Gesture> {
        if self.last_emit_valid && now_ms.saturating_sub(self.last_emit_ms) < MIN_EVENT_GAP_MS {
            return None;
        }
        self.last_emit_ms = now_ms;
        self.last_emit_valid = true;
        Some(gesture)
    }
}

fn clamp_duration(duration_ms: u64) -> u32 {
    if duration_ms > HOLD_DURATION_MAX_MS as u64 {
        HOLD_DURATION_MAX_MS
    } else {
        duration_ms as u32
    }
}

/// Longest uplink frame the firmware will ever build (a hold with its duration).
pub const INPUT_FRAME_CAPACITY: usize = 96;

/// Serialize a gesture into a single newline-terminated device -> host `input`
/// frame (see `PLAN_v2.md` "Wire Protocol").
///
/// Frames are intentionally minimal; the host timestamps events on receipt, so
/// no device clock is sent. Returns the rendered line, or `None` if it somehow
/// did not fit — the closed gesture set always fits, but the firmware must never
/// panic on a full buffer.
pub fn encode_input_frame(gesture: Gesture) -> Option<heapless::String<INPUT_FRAME_CAPACITY>> {
    use core::fmt::Write;

    let mut line = heapless::String::<INPUT_FRAME_CAPACITY>::new();
    let result = match gesture {
        Gesture::Tap => line
            .write_str("{\"v\":1,\"kind\":\"input\",\"source\":\"touch\",\"gesture\":\"tap\"}\n"),
        Gesture::DoubleTap => line.write_str(
            "{\"v\":1,\"kind\":\"input\",\"source\":\"touch\",\"gesture\":\"doubletap\"}\n",
        ),
        Gesture::Hold { duration_ms } => writeln!(
            line,
            "{{\"v\":1,\"kind\":\"input\",\"source\":\"touch\",\"gesture\":\"hold\",\"duration_ms\":{duration_ms}}}"
        ),
    };
    result.ok().map(|()| line)
}

#[cfg(test)]
mod tests {
    use super::*;

    const STEP_MS: u64 = 5;

    /// Drive the classifier at a fixed cadence while holding `pressed` for
    /// `duration_ms`, collecting any gestures emitted along the way.
    fn feed(
        classifier: &mut TouchClassifier,
        clock: &mut u64,
        pressed: bool,
        duration_ms: u64,
        out: &mut Vec<Gesture>,
    ) {
        let end = *clock + duration_ms;
        while *clock < end {
            if let Some(gesture) = classifier.update(pressed, *clock) {
                out.push(gesture);
            }
            *clock += STEP_MS;
        }
    }

    fn run(script: &[(bool, u64)]) -> Vec<Gesture> {
        let mut classifier = TouchClassifier::new();
        let mut clock = 0u64;
        let mut out = Vec::new();
        // Seed the debounce baseline as released.
        classifier.update(false, clock);
        clock += STEP_MS;
        for &(level, duration_ms) in script {
            feed(&mut classifier, &mut clock, level, duration_ms, &mut out);
        }
        out
    }

    #[test]
    fn single_short_press_is_a_tap_after_the_window() {
        // 100ms press, then idle long enough for the doubletap window to expire.
        let gestures = run(&[(true, 100), (false, 600)]);
        assert_eq!(gestures, vec![Gesture::Tap]);
    }

    #[test]
    fn tap_is_not_emitted_before_the_doubletap_window() {
        let mut classifier = TouchClassifier::new();
        let mut clock = 0u64;
        let mut out = Vec::new();
        classifier.update(false, clock);
        clock += STEP_MS;
        feed(&mut classifier, &mut clock, true, 100, &mut out);
        // Released, but only briefly: still inside the doubletap window.
        feed(&mut classifier, &mut clock, false, 200, &mut out);
        assert!(out.is_empty(), "tap must wait for the doubletap window");
        // Now let the window elapse.
        feed(&mut classifier, &mut clock, false, 400, &mut out);
        assert_eq!(out, vec![Gesture::Tap]);
    }

    #[test]
    fn long_press_is_a_hold_with_final_duration_on_release() {
        let gestures = run(&[(true, 900), (false, 600)]);
        match gestures.as_slice() {
            [Gesture::Hold { duration_ms }] => {
                // The hold is timed from debounced edges; allow a small slack
                // around the 900ms contact for the 40ms debounce + 5ms sampling.
                assert!(
                    (820..=940).contains(duration_ms),
                    "unexpected hold duration {duration_ms}"
                );
            }
            other => panic!("expected a single hold, got {other:?}"),
        }
    }

    #[test]
    fn hold_threshold_boundary_is_a_hold() {
        // Exactly at the 700ms threshold (plus debounce/sampling slack) is a hold.
        let gestures = run(&[(true, 760), (false, 600)]);
        assert!(
            matches!(gestures.as_slice(), [Gesture::Hold { .. }]),
            "press at the hold threshold should be a hold, got {gestures:?}"
        );
    }

    #[test]
    fn press_just_under_threshold_is_a_tap() {
        // ~600ms contact: comfortably a tap, never a hold.
        let gestures = run(&[(true, 600), (false, 600)]);
        assert_eq!(gestures, vec![Gesture::Tap]);
    }

    #[test]
    fn two_quick_taps_are_a_doubletap() {
        // Second press starts well within the 400ms window of the first release.
        let gestures = run(&[(true, 80), (false, 120), (true, 80), (false, 600)]);
        assert_eq!(gestures, vec![Gesture::DoubleTap]);
    }

    #[test]
    fn doubletap_then_settle_does_not_emit_a_trailing_tap() {
        // After a doubletap, the second press's release must be swallowed.
        let gestures = run(&[(true, 80), (false, 120), (true, 80), (false, 1000)]);
        assert_eq!(gestures, vec![Gesture::DoubleTap]);
    }

    #[test]
    fn two_separated_taps_are_two_taps() {
        // Each tap's release is followed by a full window of idle, so they are
        // distinct taps rather than a doubletap.
        let gestures = run(&[(true, 80), (false, 600), (true, 80), (false, 600)]);
        assert_eq!(gestures, vec![Gesture::Tap, Gesture::Tap]);
    }

    #[test]
    fn second_tap_inside_window_is_a_doubletap() {
        // ~350ms inter-tap gap (just inside the 400ms window) -> doubletap.
        let gestures = run(&[(true, 80), (false, 350), (true, 80), (false, 600)]);
        assert_eq!(gestures, vec![Gesture::DoubleTap]);
    }

    #[test]
    fn second_tap_outside_window_is_two_taps() {
        // ~450ms inter-tap gap (just past the window): the first tap fires on the
        // window timeout, the second becomes its own tap.
        let gestures = run(&[(true, 80), (false, 450), (true, 80), (false, 600)]);
        assert_eq!(gestures, vec![Gesture::Tap, Gesture::Tap]);
    }

    #[test]
    fn second_tap_at_window_boundary_never_loses_the_first_tap() {
        // A second press landing exactly on the window boundary must not silently
        // drop the pending first tap (regression test for the on_press/on_tick
        // race at the boundary).
        let gestures = run(&[(true, 80), (false, 400), (true, 80), (false, 600)]);
        assert_eq!(gestures, vec![Gesture::Tap, Gesture::Tap]);
    }

    #[test]
    fn sub_debounce_glitch_is_ignored() {
        // A 20ms blip never crosses the 40ms debounce, so no edge is accepted.
        let gestures = run(&[(true, 20), (false, 800)]);
        assert!(
            gestures.is_empty(),
            "debounce must swallow the glitch, got {gestures:?}"
        );
    }

    #[test]
    fn rate_limit_drops_a_second_doubletap_in_the_gap_window() {
        // Two doubletaps mashed back to back. Each press/release is long enough
        // to clear the 40ms debounce, but the two doubletaps land within
        // MIN_EVENT_GAP_MS of each other, so the second is dropped.
        let gestures = run(&[
            (true, 60),
            (false, 60),
            (true, 60), // -> DoubleTap #1
            (false, 60),
            (true, 60),
            (false, 60),
            (true, 60), // -> DoubleTap #2 (rate-limited away)
            (false, 600),
        ]);
        assert_eq!(
            gestures,
            vec![Gesture::DoubleTap],
            "the second doubletap should be rate-limited"
        );
    }

    #[test]
    fn hold_is_never_rate_limited_after_a_doubletap() {
        // A doubletap immediately followed by a deliberate hold: the hold must
        // still be delivered even though it is a separate gesture.
        let gestures = run(&[
            (true, 80),
            (false, 120),
            (true, 80), // DoubleTap
            (false, 120),
            (true, 900), // Hold — must not be dropped
            (false, 600),
        ]);
        assert!(
            matches!(
                gestures.as_slice(),
                [Gesture::DoubleTap, Gesture::Hold { .. }]
            ),
            "expected doubletap then hold, got {gestures:?}"
        );
    }

    #[test]
    fn very_long_press_clamps_the_reported_duration() {
        assert_eq!(clamp_duration(10 * 60 * 1000), HOLD_DURATION_MAX_MS);
        assert_eq!(clamp_duration(1234), 1234);
    }

    #[test]
    fn encodes_tap_frame() {
        let line = encode_input_frame(Gesture::Tap).unwrap();
        assert_eq!(
            line.as_str(),
            "{\"v\":1,\"kind\":\"input\",\"source\":\"touch\",\"gesture\":\"tap\"}\n"
        );
    }

    #[test]
    fn encodes_doubletap_frame() {
        let line = encode_input_frame(Gesture::DoubleTap).unwrap();
        assert_eq!(
            line.as_str(),
            "{\"v\":1,\"kind\":\"input\",\"source\":\"touch\",\"gesture\":\"doubletap\"}\n"
        );
    }

    #[test]
    fn encodes_hold_frame_with_duration() {
        let line = encode_input_frame(Gesture::Hold { duration_ms: 960 }).unwrap();
        assert_eq!(
            line.as_str(),
            "{\"v\":1,\"kind\":\"input\",\"source\":\"touch\",\"gesture\":\"hold\",\"duration_ms\":960}\n"
        );
    }
}
