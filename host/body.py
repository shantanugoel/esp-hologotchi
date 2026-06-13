"""Body-state continuity for Mochi (Phase V2a).

A thin, deterministic discrete layer over the continuous affect drives. Body
state answers "is Mochi awake, drowsing, asleep, or waking up right now?" with
real physical inertia: a nap lasts a believable stretch, waking passes through a
transition instead of snapping to excitement, and sleep only happens when there
is a real reason (tiredness, the owner being away, or late night).

The model never invents a second sleepiness signal. It reads ``Affect`` (energy,
sleepiness) and the presence report, and entering ``sleeping`` keeps affect
consistent by clamping the rendered behavior to the existing nap animation so the
normal ``Affect.register_behavior`` hook recovers energy and lowers sleepiness.

Everything here is pure and testable with explicit timestamps; the LLM proposes a
next body state, but the host validates it against these continuity rules and
softens invalid transitions instead of treating them as fatal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .affect import Affect
from .presence import PresenceReport
from .protocol import ANIMATION_TO_MOOD, BehaviorCommand


class BodyState(str, Enum):
    AWAKE = "awake"
    DROWSY = "drowsy"
    SLEEPING = "sleeping"
    WAKING = "waking"


BODY_STATE_VALUES = frozenset(state.value for state in BodyState)

# Animations that stay coherent with a given non-awake body state. ``awake`` is
# intentionally unconstrained here: the affect/presence model already drives it.
SLEEP_ANIMATIONS: tuple[str, ...] = ("nap", "sleepy", "blink")
# Gentle mid-sleep beats: kept as-is while already asleep so a long nap does not
# keep re-applying deep-nap affect recovery (see _clamp_behavior).
_SLEEP_BEAT = frozenset({"sleepy", "blink"})
DROWSY_ANIMATIONS: tuple[str, ...] = ("sleepy", "nap", "blink", "look_around", "idle")
WAKING_ANIMATIONS: tuple[str, ...] = ("sleepy", "blink", "look_around", "idle")

_ANIMATIONS_FOR_STATE: dict[BodyState, tuple[str, ...]] = {
    BodyState.SLEEPING: SLEEP_ANIMATIONS,
    BodyState.DROWSY: DROWSY_ANIMATIONS,
    BodyState.WAKING: WAKING_ANIMATIONS,
}
_DEFAULT_ANIMATION: dict[BodyState, str] = {
    BodyState.SLEEPING: "nap",
    BodyState.DROWSY: "sleepy",
    BodyState.WAKING: "sleepy",
}

# Canonical render order so prompt lists are stable. ``alert`` is included so it
# can be offered in the (rare) alert context; it is filtered out otherwise.
_CANONICAL_ANIMATIONS: tuple[str, ...] = (
    "idle", "blink", "look_around", "walk", "happy",
    "play", "excited", "sleepy", "nap", "worried", "alert",
)

# Sources that justify interrupting sleep / keeping Mochi awake.
WAKE_TRIGGER_SOURCES = frozenset({"important_alert", "direct_message", "touch"})

# "Fully rested": Mochi has no physical reason to keep sleeping. Used to forbid
# the incoherent "sleeping with max energy / zero sleepiness" state the plan bans.
WELL_RESTED_ENERGY = 85.0
WELL_RESTED_SLEEPINESS = 20.0


@dataclass(frozen=True)
class BodyConfig:
    settle_seconds: float = 60.0
    drowsy_after_seconds: float = 180.0
    min_nap_seconds: float = 360.0
    waking_seconds: float = 20.0
    late_night_start_hour: int = 22
    late_night_end_hour: int = 6

    def __post_init__(self) -> None:
        for name in (
            "settle_seconds",
            "drowsy_after_seconds",
            "min_nap_seconds",
            "waking_seconds",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True)
class BodySituation:
    """A read-only snapshot of body context for the current tick."""

    state: BodyState
    seconds_in_state: float
    asleep_seconds: float
    min_nap_elapsed: bool
    sleep_pressure: bool
    wake_trigger: bool
    late_night: bool
    allowed_states: tuple[BodyState, ...]
    allowed_animations: tuple[str, ...]
    default_state: BodyState


@dataclass
class BodyModel:
    state: BodyState = BodyState.AWAKE
    state_since: float = 0.0
    sleep_started_at: float = 0.0
    last_touch_at: float = 0.0
    config: BodyConfig = field(default_factory=BodyConfig)

    # -- per-tick evaluation ------------------------------------------------

    def advance(
        self,
        now: float,
        *,
        affect: Affect,
        report: PresenceReport | None,
        event_source: str,
        local_hour: int,
    ) -> BodySituation:
        """Apply forced transitions and compute the allowed/default options."""

        if self.state_since <= 0:
            self.state_since = now
        if self.state is BodyState.SLEEPING and self.sleep_started_at <= 0:
            self.sleep_started_at = now

        # Waking is transient: once it has run its course Mochi is simply awake.
        if (
            self.state is BodyState.WAKING
            and now - self.state_since >= self.config.waking_seconds
        ):
            self.state = BodyState.AWAKE
            self.state_since = now

        away = bool(report and report.away)
        engaged = bool(report and report.engaged)
        returned = bool(report and report.returned_from_away)
        # A *passive* return (presence/idle reclassification) is an emotional
        # reunion that may perk straight up from sleep. A return that coincides
        # with active owner contact (a touch or direct message — which itself
        # resets the engaged/return state) still wakes gently through waking, so
        # demo moment 6 (touch -> no instant chaos) holds even after a long away.
        reunion_perk = returned and event_source not in WAKE_TRIGGER_SOURCES
        late_night = self._is_late_night(local_hour)
        well_rested = (
            affect.energy >= WELL_RESTED_ENERGY
            and affect.sleepiness <= WELL_RESTED_SLEEPINESS
        )
        # Away is a reason to rest only while Mochi is actually tired; a fully
        # rested pet that is merely alone should not be pushed back to sleep.
        sleep_pressure = affect.is_sleepy() or late_night or (away and not well_rested)
        wake_trigger = event_source in WAKE_TRIGGER_SOURCES or returned

        seconds_in_state = max(0.0, now - self.state_since)
        asleep_seconds = (
            max(0.0, now - self.sleep_started_at)
            if self.state is BodyState.SLEEPING
            else 0.0
        )
        min_nap_elapsed = (
            self.state is BodyState.SLEEPING
            and asleep_seconds >= self.config.min_nap_seconds
        )
        settle_ready = seconds_in_state >= self.config.settle_seconds
        drowsy_ready = seconds_in_state >= self.config.drowsy_after_seconds

        allowed, default = _evaluate(
            self.state,
            sleep_pressure=sleep_pressure,
            wake_trigger=wake_trigger,
            reunion_perk=reunion_perk,
            engaged=engaged,
            well_rested=well_rested,
            min_nap_elapsed=min_nap_elapsed,
            settle_ready=settle_ready,
            drowsy_ready=drowsy_ready,
        )
        allowed_animations = _allowed_animations(
            allowed, affect, report, allow_alert=event_source == "important_alert"
        )

        return BodySituation(
            state=self.state,
            seconds_in_state=seconds_in_state,
            asleep_seconds=asleep_seconds,
            min_nap_elapsed=min_nap_elapsed,
            sleep_pressure=sleep_pressure,
            wake_trigger=wake_trigger,
            late_night=late_night,
            allowed_states=allowed,
            allowed_animations=allowed_animations,
            default_state=default,
        )

    # -- proposal resolution ------------------------------------------------

    def resolve(
        self,
        situation: BodySituation,
        behavior: BehaviorCommand,
        proposed_body_state: str | None,
        *,
        now: float,
    ) -> tuple[BehaviorCommand, BodyState]:
        """Pick the final body state and clamp the behavior to stay coherent.

        An invalid or missing proposal is softened to the deterministic default
        rather than rejected. An alert always wins: Mochi cannot raise an alert
        while "asleep", so alerts force ``awake``.
        """

        target = situation.default_state
        if proposed_body_state is not None:
            proposed = _coerce_state(proposed_body_state)
            if proposed is not None and proposed in situation.allowed_states:
                target = proposed
        if behavior.alert:
            target = BodyState.AWAKE

        entered_sleep = (
            target is BodyState.SLEEPING and self.state is not BodyState.SLEEPING
        )
        self._set_state(target, now)
        adjusted = _clamp_behavior(behavior, target, entered_sleep)
        return adjusted, target

    def _set_state(self, target: BodyState, now: float) -> None:
        if target is self.state:
            return
        self.state = target
        self.state_since = now
        if target is BodyState.SLEEPING:
            self.sleep_started_at = now
        else:
            self.sleep_started_at = 0.0

    def _is_late_night(self, local_hour: int) -> bool:
        start = self.config.late_night_start_hour
        end = self.config.late_night_end_hour
        if start <= end:
            return start <= local_hour < end
        return local_hour >= start or local_hour < end

    # -- persistence --------------------------------------------------------

    def to_row(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "state_since": float(self.state_since),
            "sleep_started_at": float(self.sleep_started_at),
            "last_touch_at": float(self.last_touch_at),
        }

    @classmethod
    def from_row(
        cls, row: dict[str, object], *, config: BodyConfig | None = None
    ) -> "BodyModel":
        state = _coerce_state(row.get("state")) or BodyState.AWAKE
        return cls(
            state=state,
            state_since=float(row.get("state_since", 0.0) or 0.0),
            sleep_started_at=float(row.get("sleep_started_at", 0.0) or 0.0),
            last_touch_at=float(row.get("last_touch_at", 0.0) or 0.0),
            config=config or BodyConfig(),
        )


def _evaluate(
    state: BodyState,
    *,
    sleep_pressure: bool,
    wake_trigger: bool,
    reunion_perk: bool,
    engaged: bool,
    well_rested: bool,
    min_nap_elapsed: bool,
    settle_ready: bool,
    drowsy_ready: bool,
) -> tuple[tuple[BodyState, ...], BodyState]:
    if state is BodyState.SLEEPING:
        if wake_trigger:
            # A wake trigger (touch, alert, direct message, meaningful return)
            # interrupts sleep, but active contact (touch/message) passes through
            # waking/drowsy rather than snapping straight to awake — no instant
            # chaos. Only a passive owner return may perk straight up; an alert is
            # forced awake in resolve().
            allowed = {BodyState.SLEEPING, BodyState.DROWSY, BodyState.WAKING}
            if reunion_perk:
                allowed.add(BodyState.AWAKE)
            return _order(allowed), BodyState.WAKING
        if not sleep_pressure and well_rested:
            # Fully rested with no reason to sleep: refuse to stay asleep so the
            # body never contradicts affect (no "sleeping with max energy").
            allowed = {BodyState.DROWSY, BodyState.WAKING, BodyState.AWAKE}
            return _order(allowed), BodyState.WAKING
        if min_nap_elapsed and not sleep_pressure:
            allowed = {BodyState.SLEEPING, BodyState.DROWSY, BodyState.WAKING, BodyState.AWAKE}
            return _order(allowed), BodyState.WAKING
        return _order({BodyState.SLEEPING, BodyState.DROWSY}), BodyState.SLEEPING

    if state is BodyState.DROWSY:
        allowed = {BodyState.DROWSY, BodyState.AWAKE}
        if wake_trigger or engaged or not sleep_pressure:
            return _order(allowed), BodyState.AWAKE
        if drowsy_ready:
            allowed |= {BodyState.SLEEPING}
            return _order(allowed), BodyState.SLEEPING
        return _order(allowed), BodyState.DROWSY

    if state is BodyState.WAKING:
        allowed = {BodyState.WAKING, BodyState.AWAKE, BodyState.DROWSY}
        default = (
            BodyState.DROWSY if sleep_pressure and not wake_trigger else BodyState.AWAKE
        )
        return _order(allowed), default

    # AWAKE
    allowed = {BodyState.AWAKE}
    if sleep_pressure and not engaged and not wake_trigger:
        allowed |= {BodyState.DROWSY}
        if settle_ready:
            return _order(allowed), BodyState.DROWSY
    return _order(allowed), BodyState.AWAKE


_STATE_ORDER = (
    BodyState.SLEEPING,
    BodyState.DROWSY,
    BodyState.WAKING,
    BodyState.AWAKE,
)


def _order(states: set[BodyState]) -> tuple[BodyState, ...]:
    return tuple(state for state in _STATE_ORDER if state in states)


def _allowed_animations(
    allowed_states: tuple[BodyState, ...],
    affect: Affect,
    report: PresenceReport | None,
    *,
    allow_alert: bool = False,
) -> tuple[str, ...]:
    options: set[str] = set()
    for state in allowed_states:
        if state is BodyState.AWAKE:
            options |= _awake_animations(affect, report)
        else:
            options |= set(_ANIMATIONS_FOR_STATE[state])
    if allow_alert:
        options.add("alert")
    else:
        options.discard("alert")
    ordered = tuple(anim for anim in _CANONICAL_ANIMATIONS if anim in options)
    return ordered or ("idle",)


def _awake_animations(
    affect: Affect, report: PresenceReport | None
) -> set[str]:
    options: set[str] = set(affect.suggested_animations())
    if report is not None:
        if report.returned_from_away or report.engaged:
            # Owner is actively interacting now (e.g. right after a touch), so an
            # energetic play response is on the table alongside a bright greeting.
            options |= {"happy", "excited", "play", "look_around"}
        if report.ignoring:
            options |= {"look_around", "walk", "play", "worried", "idle"}
    options.add("idle")
    return options


def _clamp_behavior(
    behavior: BehaviorCommand, target: BodyState, entered_sleep: bool
) -> BehaviorCommand:
    if target is BodyState.AWAKE:
        return behavior

    allowed = _ANIMATIONS_FOR_STATE[target]
    if target is BodyState.SLEEPING and entered_sleep:
        # The "falling asleep" frame is a real nap so affect recovers via the
        # existing register_behavior("nap") hook.
        animation = "nap"
    elif target is BodyState.SLEEPING:
        # Mid-sleep beats stay gentle ("sleepy"/"blink"); a repeated deep "nap"
        # would re-trigger full nap recovery every tick and could spike energy to
        # 100 while still asleep. Only the entering frame is a real nap.
        animation = behavior.animation if behavior.animation in _SLEEP_BEAT else "sleepy"
    elif behavior.animation in allowed:
        animation = behavior.animation
    else:
        animation = _DEFAULT_ANIMATION[target]

    if animation == behavior.animation and not behavior.alert:
        return behavior

    return BehaviorCommand(
        mood=ANIMATION_TO_MOOD[animation],
        animation=animation,
        text=behavior.text if animation == behavior.animation else None,
        alert=False,
        duration_ms=behavior.duration_ms,
    )


def _coerce_state(value: object) -> BodyState | None:
    if isinstance(value, BodyState):
        return value
    if isinstance(value, str):
        try:
            return BodyState(value)
        except ValueError:
            return None
    return None
