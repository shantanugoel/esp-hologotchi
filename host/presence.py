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
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable

Clock = Callable[[], float]

# Default time-to-live for an explicit presence source (e.g. an AirPods helper)
# and for the host-activity source. Each source expires on its own TTL so two
# independent posters never clobber each other.
DEFAULT_PRESENCE_TTL_SECONDS = 30.0
DEFAULT_ACTIVITY_TTL_SECONDS = 90.0
DEFAULT_AWAY_IDLE_SECONDS = 300.0
MAX_TTL_SECONDS = 3600.0
ACTIVITY_SOURCE = "host_activity"


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
    # Fused explicit presence from any non-activity source (e.g. AirPods). None
    # means no fresh explicit presence; the tracker then falls back to activity.
    present: bool | None = None

    def is_empty(self) -> bool:
        return (
            self.idle_seconds is None
            and self.screen_locked is None
            and self.foreground_app is None
            and self.present is None
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
    just_left: bool = False

    @property
    def ignoring(self) -> bool:
        return self.state is PresenceState.PRESENT_IGNORING

    @property
    def away(self) -> bool:
        return self.state is PresenceState.AWAY

    @property
    def engaged(self) -> bool:
        return self.state is PresenceState.ENGAGED


@dataclass
class _ActivityRecord:
    signals: PresenceSignals
    received_at: float
    ttl_seconds: float

    def expired(self, now: float) -> bool:
        return now - self.received_at > self.ttl_seconds


@dataclass
class _PresenceRecord:
    present: bool
    received_at: float
    ttl_seconds: float

    def expired(self, now: float) -> bool:
        return now - self.received_at > self.ttl_seconds


class SignalMailbox:
    """Thread-safe, source-keyed presence store with per-source TTL.

    Two independent posters share ``/presence``: a host-activity helper (idle
    time, screen lock, foreground app) and one or more explicit-presence sources
    (e.g. AirPods). Each source is stored under its own key with a ``received_at``
    stamp and a TTL so a fresh post from one never erases the other and a stale
    contribution expires on its own. The pet loop reads a fused view once per
    tick via :meth:`get`.
    """

    def __init__(
        self,
        *,
        now: Clock = time.time,
        activity_ttl_seconds: float = DEFAULT_ACTIVITY_TTL_SECONDS,
        away_idle_seconds: float = DEFAULT_AWAY_IDLE_SECONDS,
    ) -> None:
        self._lock = threading.Lock()
        self._now = now
        self._activity_ttl = activity_ttl_seconds
        self._away_idle_seconds = away_idle_seconds
        self._activity: _ActivityRecord | None = None
        self._presence: dict[str, _PresenceRecord] = {}

    def set(self, signals: PresenceSignals) -> bool:
        """Backward-compatible host-activity update."""

        return self.set_activity(signals)

    def set_activity(
        self,
        signals: PresenceSignals,
        *,
        ttl_seconds: float | None = None,
        now: float | None = None,
    ) -> bool:
        stamp = self._now() if now is None else now
        ttl = self._activity_ttl if ttl_seconds is None else ttl_seconds
        with self._lock:
            before = self._coarse_presence(stamp)
            self._activity = _ActivityRecord(
                signals=signals, received_at=stamp, ttl_seconds=ttl
            )
            after = self._coarse_presence(stamp)
        return before != after

    def set_presence(
        self,
        source: str,
        present: bool,
        *,
        ttl_seconds: float = DEFAULT_PRESENCE_TTL_SECONDS,
        now: float | None = None,
    ) -> bool:
        stamp = self._now() if now is None else now
        with self._lock:
            before = self._coarse_presence(stamp)
            self._presence[source] = _PresenceRecord(
                present=present, received_at=stamp, ttl_seconds=ttl_seconds
            )
            after = self._coarse_presence(stamp)
        return before != after

    def get(self, now: float | None = None) -> PresenceSignals:
        stamp = self._now() if now is None else now
        with self._lock:
            self._prune(stamp)
            idle = locked = None
            foreground = None
            if self._activity is not None:
                signals = self._activity.signals
                idle = signals.idle_seconds
                locked = signals.screen_locked
                foreground = signals.foreground_app
            present = self._fused_present(stamp)
        return PresenceSignals(
            idle_seconds=idle,
            screen_locked=locked,
            foreground_app=foreground,
            present=present,
        )

    def next_expiry_at(self, now: float | None = None) -> float | None:
        """Earliest future moment a fresh source will expire, or None.

        The pet loop caps its idle wait to this so an expiring presence source
        (e.g. AirPods going stale) is noticed promptly instead of up to a minute
        later.
        """

        stamp = self._now() if now is None else now
        soonest: float | None = None
        with self._lock:
            records: list[_ActivityRecord | _PresenceRecord] = list(
                self._presence.values()
            )
            if self._activity is not None:
                records.append(self._activity)
            for record in records:
                if record.expired(stamp):
                    continue
                expiry = record.received_at + record.ttl_seconds
                if soonest is None or expiry < soonest:
                    soonest = expiry
        return soonest

    def _prune(self, now: float) -> None:
        if self._activity is not None and self._activity.expired(now):
            self._activity = None
        expired = [key for key, rec in self._presence.items() if rec.expired(now)]
        for key in expired:
            del self._presence[key]

    def _coarse_presence(self, now: float) -> str | None:
        """A cheap away/present label used only to decide whether to wake the loop.

        This is not the full classification (the tracker owns engaged/ignoring);
        it just answers "did a meaningful presence transition happen?" so both
        explicit-presence flips and host-activity transitions (lock, idle crossing
        the away threshold, or a source appearing/expiring) interrupt the wait.
        """

        present = self._fused_present(now)
        if present is False:
            return "away"
        if present is True:
            return "present"
        if self._activity is not None and not self._activity.expired(now):
            signals = self._activity.signals
            if signals.screen_locked:
                return "away"
            if signals.idle_seconds is not None:
                return "away" if signals.idle_seconds >= self._away_idle_seconds else "present"
            if signals.screen_locked is False or signals.foreground_app is not None:
                return "present"
        return None

    def _fused_present(self, now: float) -> bool | None:
        """Fuse explicit presence sources. Absence is authoritative.

        Any fresh source reporting ``present=false`` wins (the owner is treated
        as away even if local input looks active). Otherwise a fresh
        ``present=true`` makes Mochi consider the owner nearby. With no fresh
        explicit source the result is ``None`` and the tracker falls back to
        host activity.
        """

        saw_true = False
        for record in self._presence.values():
            if record.expired(now):
                continue
            if record.present is False:
                return False
            saw_true = True
        return True if saw_true else None


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

        just_left = (
            new_state is PresenceState.AWAY
            and previous is not None
            and previous is not PresenceState.AWAY
        )

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
            just_left=just_left,
        )

    def _classify(self, now: float, signals: PresenceSignals) -> PresenceState:
        # Precedence (fixed order):
        # 1. A recent explicit interaction always wins: you can interact without
        #    AirPods.
        if (
            self.last_interaction > 0
            and now - self.last_interaction <= self.config.engaged_window_seconds
        ):
            return PresenceState.ENGAGED
        # 2/3. Fresh explicit presence is authoritative over host activity: an
        #    explicit absence reads as away even if local input looks active.
        if signals.present is False:
            return PresenceState.AWAY
        if signals.present is True:
            return PresenceState.PRESENT_IGNORING
        # 4. No fresh explicit presence: fall back to host activity.
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
