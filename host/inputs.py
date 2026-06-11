from __future__ import annotations

import queue
from dataclasses import dataclass
from itertools import count

DIRECT_MESSAGE_MAX_LEN = 500


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

    def submit_direct_message(self, text: str) -> HostInput:
        message = _clean_direct_message(text)
        item = HostInput(
            id=f"direct-{next(self._ids)}",
            source="direct_message",
            event=f"Direct user message: {message}",
        )
        self._queue.put(item)
        return item

    def get_nowait(self) -> HostInput | None:
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    def wait(self, timeout_seconds: float) -> HostInput | None:
        try:
            return self._queue.get(timeout=timeout_seconds)
        except queue.Empty:
            return None


def _clean_direct_message(text: str) -> str:
    if not isinstance(text, str):
        raise InputError("text must be a string")

    cleaned = " ".join(text.split())
    if not cleaned:
        raise InputError("text must not be empty")
    if len(cleaned) > DIRECT_MESSAGE_MAX_LEN:
        raise InputError(
            f"text must be at most {DIRECT_MESSAGE_MAX_LEN} characters after trimming"
        )

    return cleaned
