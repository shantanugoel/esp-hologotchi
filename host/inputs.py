from __future__ import annotations

import math
import queue
import threading
from dataclasses import dataclass
from itertools import count

DIRECT_MESSAGE_MAX_LEN = 500
INDIRECT_DETAIL_MAX_LEN = 240
ALERT_TEXT_MAX_LEN = 240
BUILD_TEST_KINDS = frozenset({"build", "test"})
TOUCH_GESTURES = frozenset({"tap", "hold", "doubletap"})
# Upper bound on a reported hold duration (ms); guards against a bogus uplink
# frame. A real TTP223 hold is at most a few seconds.
TOUCH_DURATION_MAX_MS = 60_000

PRESENCE_SIGNAL_SOURCE = "presence_signal"
TOUCH_SOURCE = "touch"


class InputError(ValueError):
    """Raised when a host-side input cannot be accepted."""


@dataclass(frozen=True)
class HostInput:
    id: str
    source: str
    event: str
    # Set only for touch inputs; carries the classified gesture so the loop can
    # apply a deterministic per-gesture effect without re-parsing the event text.
    gesture: str | None = None


class HostInputQueue:
    def __init__(self) -> None:
        self._queue: queue.Queue[HostInput] = queue.Queue()
        self._ids = count(1)
        self._lock = threading.Lock()
        self._presence_pending = False

    def submit_direct_message(self, text: str) -> HostInput:
        message = _clean_direct_message(text)
        item = HostInput(
            id=f"direct-{self._next_id()}",
            source="direct_message",
            event=f"Direct user message: {message}",
        )
        self._queue.put(item)
        return item

    def submit_build_test_result(
        self, kind: str, ok: bool, detail: str | None = None
    ) -> HostInput:
        clean_kind = _clean_build_test_kind(kind)
        if not isinstance(ok, bool):
            raise InputError("ok must be a boolean")

        clean_detail = _clean_optional_text(
            detail,
            field="text",
            max_len=INDIRECT_DETAIL_MAX_LEN,
        )
        status = "passed" if ok else "failed"
        label = clean_kind.capitalize()
        event = f"{label} {status}."
        if clean_detail is not None:
            event = f"{event} {clean_detail}"

        item = HostInput(
            id=f"{clean_kind}-{self._next_id()}",
            source=f"{clean_kind}_result",
            event=event,
        )
        self._queue.put(item)
        return item

    def submit_important_alert(self, text: str) -> HostInput:
        message = _clean_required_text(
            text,
            field="text",
            max_len=ALERT_TEXT_MAX_LEN,
        )
        item = HostInput(
            id=f"alert-{self._next_id()}",
            source="important_alert",
            event=f"Important alert: {message}",
        )
        self._queue.put(item)
        return item

    def submit_touch(
        self, gesture: str, duration_ms: int | float | None = None
    ) -> HostInput:
        """Enqueue a physical-contact event (tap / hold / doubletap).

        ``duration_ms`` is only meaningful for a ``hold`` and is folded into the
        human-readable situation text; the per-gesture effect itself is
        deterministic and does not scale with duration.
        """

        clean_gesture = _clean_touch_gesture(gesture)
        clean_duration = _clean_touch_duration(duration_ms)
        item = HostInput(
            id=f"touch-{self._next_id()}",
            source=TOUCH_SOURCE,
            event=_touch_event_text(clean_gesture, clean_duration),
            gesture=clean_gesture,
        )
        self._queue.put(item)
        return item

    def submit_presence_signal(self) -> HostInput | None:
        """Wake the loop for a meaningful presence transition.

        The event carries no detail: the loop rebuilds the situation text from
        the freshly reclassified presence report. Duplicates are coalesced — only
        one presence signal is ever pending at a time so rapid ``/presence`` posts
        cannot spam the loop.
        """

        with self._lock:
            if self._presence_pending:
                return None
            self._presence_pending = True
            item = HostInput(
                id=f"presence-{next(self._ids)}",
                source=PRESENCE_SIGNAL_SOURCE,
                event="Presence changed.",
            )
        self._queue.put(item)
        return item

    def get_nowait(self) -> HostInput | None:
        try:
            return self._consume(self._queue.get_nowait())
        except queue.Empty:
            return None

    def wait(self, timeout_seconds: float) -> HostInput | None:
        try:
            return self._consume(self._queue.get(timeout=timeout_seconds))
        except queue.Empty:
            return None

    def _consume(self, item: HostInput) -> HostInput:
        if item.source == PRESENCE_SIGNAL_SOURCE:
            with self._lock:
                self._presence_pending = False
        return item

    def _next_id(self) -> int:
        with self._lock:
            return next(self._ids)


def _clean_direct_message(text: str) -> str:
    return _clean_required_text(
        text,
        field="text",
        max_len=DIRECT_MESSAGE_MAX_LEN,
    )


def _clean_build_test_kind(kind: str) -> str:
    if not isinstance(kind, str):
        raise InputError("kind must be a string")
    cleaned = kind.strip().lower()
    if cleaned not in BUILD_TEST_KINDS:
        allowed = ", ".join(sorted(BUILD_TEST_KINDS))
        raise InputError(f"kind must be one of: {allowed}")
    return cleaned


def _clean_touch_gesture(gesture: str) -> str:
    if not isinstance(gesture, str):
        raise InputError("gesture must be a string")
    cleaned = gesture.strip().lower()
    if cleaned not in TOUCH_GESTURES:
        allowed = ", ".join(sorted(TOUCH_GESTURES))
        raise InputError(f"gesture must be one of: {allowed}")
    return cleaned


def _clean_touch_duration(duration_ms: int | float | None) -> int | None:
    if duration_ms is None:
        return None
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, (int, float)):
        raise InputError("duration_ms must be a number")
    if not math.isfinite(duration_ms):
        raise InputError("duration_ms must be a finite number")
    if duration_ms < 0:
        raise InputError("duration_ms must not be negative")
    return min(int(duration_ms), TOUCH_DURATION_MAX_MS)


def _touch_event_text(gesture: str, duration_ms: int | None) -> str:
    if gesture == "hold":
        if duration_ms is not None:
            return f"Touch input: a gentle, soothing pet hold for {duration_ms}ms."
        return "Touch input: a gentle, soothing pet hold."
    if gesture == "doubletap":
        return "Touch input: a double-tap play invite."
    return "Touch input: a quick boop tap to say hello."


def _clean_required_text(text: str, *, field: str, max_len: int) -> str:
    if not isinstance(text, str):
        raise InputError(f"{field} must be a string")

    cleaned = " ".join(text.split())
    if not cleaned:
        raise InputError(f"{field} must not be empty")
    if len(cleaned) > max_len:
        raise InputError(f"{field} must be at most {max_len} characters after trimming")

    return cleaned


def _clean_optional_text(text: str | None, *, field: str, max_len: int) -> str | None:
    if text is None:
        return None
    return _clean_required_text(text, field=field, max_len=max_len)
