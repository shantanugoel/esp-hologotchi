"""Presence state machine for Mochi (Phase 9b).

"How does it know it's being ignored?" is a first-class requirement. The owner's
computer is the sensor. This module turns cheap, opt-in, host-side signals (OS
idle time, screen-lock state, foreground app) into one of three presence states:

- ``away`` — no host activity or the screen is locked. A genuine absence is not
  treated as rejection.
- ``present_but_ignoring`` — the owner is using the computer but has not
  interacted with Mochi for a while. This is real "ignored" and it grows
  loneliness over time.
- ``engaged`` — a recent direct interaction (message, boop, acknowledged alert).

Everything here is deterministic and testable with an injected clock. Signals
are fed in from outside (e.g. an opt-in helper posting to the control server), so
no platform-specific probing lives in the core loop.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum


class PresenceState(str, Enum):
    ENGAGED = "engaged"
    PRESENT_IGNORING = "present_but_ignoring"
    AWAY = "away"


@dataclass(frozen=True)
class PresenceConfig:
    engaged_window_seconds: float = 90.0
    away_idle_seconds: float = 300.0
    return_min_seconds: float = 120.0
    focus_jealousy_seconds: float = 1200.0

    def __post_init__(self) -> None:
        for name in (
            "engaged_window_seconds",
            "away_idle_seconds",
            "return_min_seconds",
            "focus_jealousy_seconds",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True)
class PresenceSignals:
    idle_seconds: float | None = None
    screen_locked: bool | None = None
    foreground_app: str | None = None

    def is_empty(self) -> bool:
        return (
            self.idle_seconds is None
            and self.screen_locked is None
            and self.foreground_app is None
        )


@dataclass(frozen=True)
class PresenceReport:
    state: PresenceState
    ignored_seconds: float
    away_seconds: float
    returned_from_away: bool
    away_before_return: float
    focus_app: str | None
    focus_seconds: float
    focus_pressure: float

    @property
    def ignoring(self) -> bool:
        return self.state is PresenceState.PRESENT_IGNORING

    @property
    def away(self) -> bool:
        return self.state is PresenceState.AWAY

    @property
    def engaged(self) -> bool:
        return self.state is PresenceState.ENGAGED


class SignalMailbox:
    """Thread-safe holder for the latest presence signals.

    The control server writes from request threads; the pet loop reads once per
    tick. The latest signal wins.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._signals = PresenceSignals()

    def set(self, signals: PresenceSignals) -> None:
        with self._lock:
            self._signals = signals

    def get(self) -> PresenceSignals:
        with self._lock:
            return self._signals


class PresenceTracker:
    def __init__(
        self,
        config: PresenceConfig | None = None,
        *,
        last_interaction: float = 0.0,
    ) -> None:
        self.config = config or PresenceConfig()
        self.last_interaction = last_interaction
        self._state: PresenceState | None = None
        self._state_since = 0.0
        self._away_since = 0.0
        self._focus_app: str | None = None
        self._focus_since = 0.0

    def note_interaction(self, now: float) -> None:
        self.last_interaction = now

    def update(self, now: float, signals: PresenceSignals) -> PresenceReport:
        new_state = self._classify(now, signals)
        self._update_focus(now, signals)

        previous = self._state
        away_before_return = 0.0
        returned = False
        if previous is PresenceState.AWAY and new_state is not PresenceState.AWAY:
            away_before_return = max(0.0, now - self._away_since)
            returned = away_before_return >= self.config.return_min_seconds

        if new_state is PresenceState.AWAY and previous is not PresenceState.AWAY:
            self._away_since = now
        if new_state is not previous:
            self._state_since = now

        away_seconds = now - self._away_since if new_state is PresenceState.AWAY else 0.0
        ignored_seconds = (
            now - self._state_since if new_state is PresenceState.PRESENT_IGNORING else 0.0
        )
        focus_seconds = now - self._focus_since if self._focus_app else 0.0
        focus_pressure = self._focus_pressure(new_state, focus_seconds)

        self._state = new_state
        return PresenceReport(
            state=new_state,
            ignored_seconds=ignored_seconds,
            away_seconds=away_seconds,
            returned_from_away=returned,
            away_before_return=away_before_return,
            focus_app=self._focus_app,
            focus_seconds=focus_seconds,
            focus_pressure=focus_pressure,
        )

    def _classify(self, now: float, signals: PresenceSignals) -> PresenceState:
        if self.last_interaction > 0 and now - self.last_interaction <= self.config.engaged_window_seconds:
            return PresenceState.ENGAGED
        if signals.screen_locked:
            return PresenceState.AWAY
        if signals.idle_seconds is not None:
            if signals.idle_seconds >= self.config.away_idle_seconds:
                return PresenceState.AWAY
            return PresenceState.PRESENT_IGNORING
        if signals.screen_locked is False:
            # Screen is explicitly unlocked but we have no idle reading: assume the
            # owner is present at the desk.
            return PresenceState.PRESENT_IGNORING
        # No evidence of presence at all. Treat as a benign absence rather than
        # rejection, so a missing presence helper or a fresh restart never makes
        # Mochi feel actively ignored.
        return PresenceState.AWAY

    def _update_focus(self, now: float, signals: PresenceSignals) -> None:
        if signals.foreground_app != self._focus_app:
            self._focus_app = signals.foreground_app
            self._focus_since = now

    def _focus_pressure(self, state: PresenceState, focus_seconds: float) -> float:
        if state is not PresenceState.PRESENT_IGNORING or not self._focus_app:
            return 0.0
        if focus_seconds < self.config.focus_jealousy_seconds:
            return 0.0
        over = focus_seconds - self.config.focus_jealousy_seconds
        return min(1.0, over / self.config.focus_jealousy_seconds)
