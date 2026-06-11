from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final

WIRE_VERSION: Final = 1
WIRE_KIND: Final = "behavior"
TEXT_MAX_LEN: Final = 24
MIN_DURATION_MS: Final = 1_000
MAX_DURATION_MS: Final = 15_000

ANIMATION_TO_MOOD: Final[dict[str, str]] = {
    "idle": "calm",
    "blink": "calm",
    "look_around": "curious",
    "happy": "happy",
    "sleepy": "sleepy",
    "worried": "worried",
    "alert": "alert",
}
ALLOWED_FIELDS: Final[frozenset[str]] = frozenset(
    {"v", "kind", "mood", "animation", "text", "alert", "duration_ms"}
)


class ValidationError(ValueError):
    """Raised when model output does not match the V1 behavior contract."""


@dataclass(frozen=True)
class BehaviorCommand:
    mood: str
    animation: str
    text: str | None
    alert: bool
    duration_ms: int

    def payload(self) -> dict[str, object]:
        return {
            "v": WIRE_VERSION,
            "kind": WIRE_KIND,
            "mood": self.mood,
            "animation": self.animation,
            "text": self.text or "",
            "alert": self.alert,
            "duration_ms": self.duration_ms,
        }

    def to_json_line(self) -> str:
        return json.dumps(self.payload(), separators=(",", ":"), ensure_ascii=True) + "\n"


def parse_behavior_response(response_text: str) -> BehaviorCommand:
    raw = _load_behavior_object(response_text)

    if not isinstance(raw, dict):
        raise ValidationError("model output must be a single JSON object")

    unexpected = set(raw) - ALLOWED_FIELDS
    if unexpected:
        raise ValidationError(f"unexpected fields in behavior JSON: {sorted(unexpected)}")

    version = raw.get("v")
    if version != WIRE_VERSION:
        raise ValidationError(f"expected v={WIRE_VERSION}, got {version!r}")

    kind = raw.get("kind")
    if kind != WIRE_KIND:
        raise ValidationError(f"expected kind={WIRE_KIND!r}, got {kind!r}")

    animation = _require_string(raw, "animation")
    if animation not in ANIMATION_TO_MOOD:
        raise ValidationError(f"unknown animation {animation!r}")

    mood = _require_string(raw, "mood")
    expected_mood = ANIMATION_TO_MOOD[animation]
    if mood != expected_mood:
        raise ValidationError(
            f"animation {animation!r} must use mood {expected_mood!r}, got {mood!r}"
        )

    alert = raw.get("alert")
    if not isinstance(alert, bool):
        raise ValidationError("alert must be a boolean")
    if alert != (animation == "alert"):
        raise ValidationError("alert flag must match the alert animation exactly")

    duration_ms = raw.get("duration_ms")
    if not isinstance(duration_ms, int) or isinstance(duration_ms, bool):
        raise ValidationError("duration_ms must be an integer")
    if not MIN_DURATION_MS <= duration_ms <= MAX_DURATION_MS:
        raise ValidationError(
            f"duration_ms must be between {MIN_DURATION_MS} and {MAX_DURATION_MS}"
        )

    text_value = raw.get("text", "")
    if text_value is None:
        text = None
    else:
        if not isinstance(text_value, str):
            raise ValidationError("text must be a string when present")
        text = text_value.strip() or None

    if text is not None:
        if not text.isascii():
            raise ValidationError("text must stay ASCII so the device can render it")
        if len(text) > TEXT_MAX_LEN:
            raise ValidationError(f"text must be at most {TEXT_MAX_LEN} characters")
        if any(not ch.isprintable() for ch in text):
            raise ValidationError("text must use printable characters only")

    return BehaviorCommand(
        mood=mood,
        animation=animation,
        text=text,
        alert=alert,
        duration_ms=duration_ms,
    )


def _require_string(raw: dict[str, object], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    return value


def _load_behavior_object(response_text: str) -> object:
    try:
        return json.loads(response_text)
    except json.JSONDecodeError as exact_exc:
        decoder = json.JSONDecoder()
        for index, char in enumerate(response_text):
            if char != "{":
                continue
            try:
                raw, _ = decoder.raw_decode(response_text, index)
            except json.JSONDecodeError:
                continue
            return raw
        if response_text.strip():
            raise ValidationError(
                f"model output did not contain a JSON object: {exact_exc}"
            ) from exact_exc
        raise ValidationError("model output was empty") from exact_exc
