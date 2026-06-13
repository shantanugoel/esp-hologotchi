from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from itertools import count

DIRECT_MESSAGE_MAX_LEN = 500
INDIRECT_DETAIL_MAX_LEN = 240
ALERT_TEXT_MAX_LEN = 240
BUILD_TEST_KINDS = frozenset({"build", "test"})

PRESENCE_SIGNAL_SOURCE = "presence_signal"


class InputError(ValueError):
    """Raised when a host-side input cannot be accepted."""


@dataclass(frozen=True)
class HostInput:
    id: str
    source: str
    event: str


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
