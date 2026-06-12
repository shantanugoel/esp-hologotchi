"""Deterministic affect model for Mochi (Phase 9a).

This is the "continuity" half of Mochi's brain: needs, drives, relationship
state, and a slow bond level. It is a pure, testable model with no model calls
and no I/O. Values are stored as continuous accumulators so that small per-tick
decay adds up correctly over a variable loop cadence, and are exposed as
integers for the prompt and for persistence.

Drives are *satisfaction* levels in ``0..100`` where 100 means the need is met
and 0 means it is desperate. They fall over real elapsed wall-clock time and are
replenished by owner actions and Mochi's own choices, so neglect has visible,
bounded, recoverable consequences.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

MIN_STAT = 0.0
MAX_STAT = 100.0

# Drive decay per minute of real elapsed time.
SOCIAL_DECAY = 0.7
PLAY_DECAY = 0.9
REST_DECAY = 0.5
STIMULATION_DECAY = 1.1
SLEEPINESS_GAIN = 0.4
ENERGY_DECAY = 0.2

# Presence multipliers (see host/presence.py).
IGNORING_SOCIAL_MULT = 2.5
AWAY_STIMULATION_MULT = 0.3

# Relationship dynamics per minute.
SOCIAL_COMFORT = 55.0
LONELINESS_RATE = 0.6
IGNORING_LONELINESS_MULT = 2.2
LONELINESS_AWAY_EASE = 0.2
FOCUS_LONELINESS_RATE = 0.8
FOCUS_FRUSTRATION_RATE = 0.5
FRUSTRATION_DECAY = 0.6
AFFECTION_BASELINE = 60.0
AFFECTION_COOL = 0.05
BOND_NEGLECT_DECAY = 0.02
NEGLECT_LONELINESS = 60.0

# Bound how much elapsed time a single advance can apply, so a host restart or a
# stalled loop catches up gently instead of nuking state in one giant step.
MAX_CATCHUP_SECONDS = 900.0

# Escalation thresholds.
WITHDRAWN_ENERGY = 25.0
DISTRESS_LEVEL = 60.0
SAD_LONELINESS = 60.0
NEEDY_LONELINESS = 40.0
NEEDY_SOCIAL = 35.0
NEEDY_PLAY = 25.0
RESTLESS_STIMULATION = 35.0
RESTLESS_PLAY = 45.0
SLEEPY_LEVEL = 70.0
BRIGHT_AFFECTION = 75.0
BRIGHT_LONELINESS = 25.0

CONTENT = "content"
RESTLESS = "restless"
NEEDY = "needy"
SAD = "sad"
GRUMPY = "grumpy"
WITHDRAWN = "withdrawn"

# State -> which of the existing 11 animations express it (renderer mapping, so
# new feelings ship with no firmware change).
SUGGESTED_ANIMATIONS: dict[str, tuple[str, ...]] = {
    CONTENT: ("idle", "blink", "look_around", "walk", "happy"),
    RESTLESS: ("walk", "look_around", "play"),
    NEEDY: ("play", "look_around", "happy", "walk"),
    SAD: ("worried", "sleepy", "idle"),
    GRUMPY: ("worried", "look_around", "walk"),
    WITHDRAWN: ("nap", "sleepy", "idle"),
}


@dataclass
class Affect:
    # Drives (satisfaction; high = content, low = needy).
    social: float = 60.0
    play: float = 55.0
    rest: float = 65.0
    stimulation: float = 55.0
    # Body.
    energy: float = 55.0
    sleepiness: float = 25.0
    # Relationship.
    affection: float = 70.0
    trust: float = 60.0
    loneliness: float = 20.0
    frustration: float = 10.0
    # Long-term arc.
    bond: float = 10.0
    # Decay marker: epoch seconds of the last advance; 0 means uninitialized.
    last_update: float = 0.0

    def advance(
        self,
        now: float,
        *,
        ignoring: bool = False,
        away: bool = False,
        focus_pressure: float = 0.0,
    ) -> None:
        """Apply deterministic decay for the time elapsed since the last call.

        ``ignoring`` and ``away`` come from the presence state machine.
        ``focus_pressure`` in ``0..1`` is grounded jealousy: the owner being
        heads-down in one app while ignoring Mochi.
        """

        if self.last_update <= 0:
            self.last_update = now
            return
        elapsed = now - self.last_update
        self.last_update = now
        if elapsed <= 0:
            return
        elapsed = min(elapsed, MAX_CATCHUP_SECONDS)

        minutes = elapsed / 60.0
        focus_pressure = max(0.0, min(1.0, focus_pressure))

        social_mult = 0.0 if away else (IGNORING_SOCIAL_MULT if ignoring else 1.0)
        self.social = _drop(self.social, SOCIAL_DECAY * minutes * social_mult)
        self.play = _drop(self.play, PLAY_DECAY * minutes)
        self.rest = _drop(self.rest, REST_DECAY * minutes)
        stim_mult = AWAY_STIMULATION_MULT if away else 1.0
        self.stimulation = _drop(self.stimulation, STIMULATION_DECAY * minutes * stim_mult)
        self.sleepiness = _rise(self.sleepiness, SLEEPINESS_GAIN * minutes)
        self.energy = _drop(self.energy, ENERGY_DECAY * minutes)

        self._advance_loneliness(minutes, ignoring=ignoring, away=away, focus_pressure=focus_pressure)

        self.frustration = _drop(self.frustration, FRUSTRATION_DECAY * minutes)
        if ignoring and focus_pressure > 0:
            self.frustration = _rise(self.frustration, focus_pressure * FOCUS_FRUSTRATION_RATE * minutes)

        self.affection = _toward(self.affection, AFFECTION_BASELINE, AFFECTION_COOL * minutes)
        if self.loneliness > NEGLECT_LONELINESS:
            self.bond = _drop(self.bond, BOND_NEGLECT_DECAY * minutes)

    def _advance_loneliness(
        self,
        minutes: float,
        *,
        ignoring: bool,
        away: bool,
        focus_pressure: float,
    ) -> None:
        if away:
            # A genuine absence is not rejection; loneliness holds or eases.
            self.loneliness = _drop(self.loneliness, LONELINESS_AWAY_EASE * minutes)
            return
        if self.social >= SOCIAL_COMFORT:
            self.loneliness = _drop(self.loneliness, LONELINESS_RATE * minutes)
            return
        deficit = (SOCIAL_COMFORT - self.social) / SOCIAL_COMFORT
        gain = LONELINESS_RATE * minutes * deficit
        if ignoring:
            gain *= IGNORING_LONELINESS_MULT
            gain += focus_pressure * FOCUS_LONELINESS_RATE * minutes
        self.loneliness = _rise(self.loneliness, gain)

    # -- Owner-driven events (replenish or repair) --------------------------

    def register_attention(self) -> None:
        self.social = _rise(self.social, 35.0)
        self.stimulation = _rise(self.stimulation, 12.0)
        self.loneliness = _drop(self.loneliness, 30.0)
        self.affection = _rise(self.affection, 4.0)
        self.trust = _rise(self.trust, 2.0)
        self.bond = _rise(self.bond, 1.0)

    def register_praise(self) -> None:
        self.register_attention()
        self.affection = _rise(self.affection, 8.0)
        self.trust = _rise(self.trust, 4.0)
        self.frustration = _drop(self.frustration, 20.0)
        self.bond = _rise(self.bond, 2.0)

    def register_harsh(self) -> None:
        self.affection = _drop(self.affection, 6.0)
        self.trust = _drop(self.trust, 4.0)
        self.frustration = _rise(self.frustration, 18.0)
        # Attention, even harsh, is still contact.
        self.social = _rise(self.social, 10.0)
        self.loneliness = _drop(self.loneliness, 8.0)

    def register_apology(self) -> None:
        self.frustration = _drop(self.frustration, 35.0)
        self.loneliness = _drop(self.loneliness, 15.0)
        self.trust = _rise(self.trust, 6.0)
        self.affection = _rise(self.affection, 4.0)
        self.social = _rise(self.social, 10.0)

    def register_good_outcome(self) -> None:
        self.stimulation = _rise(self.stimulation, 12.0)
        self.frustration = _drop(self.frustration, 14.0)
        self.affection = _rise(self.affection, 3.0)
        self.trust = _rise(self.trust, 3.0)
        self.bond = _rise(self.bond, 1.0)

    def register_bad_outcome(self) -> None:
        self.frustration = _rise(self.frustration, 16.0)
        self.energy = _drop(self.energy, 4.0)

    def register_alert(self) -> None:
        self.stimulation = _rise(self.stimulation, 10.0)
        self.energy = _rise(self.energy, 8.0)

    def register_return_after_away(self, away_seconds: float) -> None:
        del away_seconds
        self.social = _rise(self.social, 20.0)
        self.stimulation = _rise(self.stimulation, 18.0)
        self.loneliness = _drop(self.loneliness, 18.0)
        self.energy = _rise(self.energy, 6.0)

    # -- Behavior-driven events (what Mochi just chose) ---------------------

    def register_behavior(self, animation: str) -> None:
        match animation:
            case "nap":
                self.rest = _rise(self.rest, 30.0)
                self.sleepiness = _drop(self.sleepiness, 35.0)
                self.energy = _rise(self.energy, 18.0)
            case "sleepy":
                self.rest = _rise(self.rest, 8.0)
                self.sleepiness = _drop(self.sleepiness, 6.0)
                self.energy = _rise(self.energy, 4.0)
            case "play":
                self.play = _rise(self.play, 26.0)
                self.stimulation = _rise(self.stimulation, 14.0)
                self.energy = _drop(self.energy, 6.0)
            case "excited":
                self.play = _rise(self.play, 18.0)
                self.stimulation = _rise(self.stimulation, 18.0)
                self.energy = _drop(self.energy, 10.0)
            case "happy":
                self.play = _rise(self.play, 10.0)
                self.stimulation = _rise(self.stimulation, 8.0)
            case "walk":
                self.stimulation = _rise(self.stimulation, 12.0)
                self.energy = _drop(self.energy, 4.0)
            case "look_around":
                self.stimulation = _rise(self.stimulation, 8.0)
            case "blink" | "idle":
                self.rest = _rise(self.rest, 2.0)

    # -- Views --------------------------------------------------------------

    def overall_state(self) -> str:
        if self.energy < WITHDRAWN_ENERGY and (
            self.loneliness > DISTRESS_LEVEL or self.frustration > DISTRESS_LEVEL
        ):
            return WITHDRAWN
        if self.frustration >= DISTRESS_LEVEL:
            return GRUMPY
        if self.loneliness >= SAD_LONELINESS:
            return SAD
        if (
            self.loneliness >= NEEDY_LONELINESS
            or self.social < NEEDY_SOCIAL
            or self.play < NEEDY_PLAY
        ):
            return NEEDY
        if self.stimulation < RESTLESS_STIMULATION or self.play < RESTLESS_PLAY:
            return RESTLESS
        return CONTENT

    def is_sleepy(self) -> bool:
        return self.sleepiness >= SLEEPY_LEVEL or self.energy < WITHDRAWN_ENERGY

    def is_bright(self) -> bool:
        return (
            self.affection >= BRIGHT_AFFECTION
            and self.loneliness < BRIGHT_LONELINESS
            and self.energy > 55.0
        )

    def suggested_animations(self) -> tuple[str, ...]:
        state = self.overall_state()
        if state in (CONTENT, RESTLESS, NEEDY) and self.is_sleepy():
            return ("sleepy", "nap", "idle")
        return SUGGESTED_ANIMATIONS[state]

    def feeling_line(self) -> str:
        state = self.overall_state()
        sleepy = " and getting sleepy" if self.is_sleepy() else ""
        descriptions = {
            CONTENT: "content and settled" if not self.is_bright() else "bright and affectionate",
            RESTLESS: "a little restless and under-stimulated",
            NEEDY: "needy and wanting attention",
            SAD: "lonely and sad after a quiet stretch",
            GRUMPY: "frustrated and a bit grumpy",
            WITHDRAWN: "low-energy and withdrawn, sulking softly",
        }
        return f"Mochi feels {descriptions[state]}{sleepy}."

    def snapshot(self) -> dict[str, int]:
        return {
            "social": _as_int(self.social),
            "play": _as_int(self.play),
            "rest": _as_int(self.rest),
            "stimulation": _as_int(self.stimulation),
            "energy": _as_int(self.energy),
            "sleepiness": _as_int(self.sleepiness),
            "affection": _as_int(self.affection),
            "trust": _as_int(self.trust),
            "loneliness": _as_int(self.loneliness),
            "frustration": _as_int(self.frustration),
            "bond": _as_int(self.bond),
        }

    def prompt_block(self) -> str:
        snap = self.snapshot()
        suggestions = ", ".join(self.suggested_animations())
        return (
            "Needs (satisfaction, 0=desperate 100=met):\n"
            f"- social: {snap['social']}/100\n"
            f"- play: {snap['play']}/100\n"
            f"- rest: {snap['rest']}/100\n"
            f"- stimulation: {snap['stimulation']}/100\n"
            f"- energy: {snap['energy']}/100\n"
            f"- sleepiness: {snap['sleepiness']}/100\n\n"
            "Relationship:\n"
            f"- affection: {snap['affection']}/100\n"
            f"- trust: {snap['trust']}/100\n"
            f"- loneliness: {snap['loneliness']}/100\n"
            f"- frustration: {snap['frustration']}/100\n"
            f"- bond level: {snap['bond']}/100\n\n"
            f"Inner state: {self.overall_state()}. {self.feeling_line()}\n"
            f"Behaviors that fit this state: {suggestions}."
        )

    # -- Persistence --------------------------------------------------------

    def to_row(self) -> dict[str, float]:
        return {f.name: float(getattr(self, f.name)) for f in fields(self)}

    @classmethod
    def from_row(cls, row: dict[str, float]) -> "Affect":
        known = {f.name for f in fields(cls)}
        return cls(**{key: float(value) for key, value in row.items() if key in known})


def _drop(value: float, amount: float) -> float:
    return _clamp(value - amount)


def _rise(value: float, amount: float) -> float:
    return _clamp(value + amount)


def _toward(value: float, target: float, amount: float) -> float:
    if value > target:
        return max(target, value - amount)
    if value < target:
        return min(target, value + amount)
    return value


def _clamp(value: float) -> float:
    return min(MAX_STAT, max(MIN_STAT, value))


def _as_int(value: float) -> int:
    return int(round(_clamp(value)))
