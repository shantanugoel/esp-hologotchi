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
    "walk": "curious",
    "happy": "happy",
    "play": "happy",
    "excited": "happy",
    "sleepy": "sleepy",
    "nap": "sleepy",
    "worried": "worried",
    "alert": "alert",
}
ALLOWED_MOODS: Final[frozenset[str]] = frozenset(ANIMATION_TO_MOOD.values())
ALLOWED_FIELDS: Final[frozenset[str]] = frozenset(
    {"v", "kind", "mood", "animation", "text", "alert", "duration_ms"}
)

# Host-only proposal extras. These never reach the device; they are stripped
# before the behavior frame is sent (see BehaviorProposal.to_behavior_command).
ALLOWED_BODY_STATES: Final[frozenset[str]] = frozenset(
    {"awake", "drowsy", "sleeping", "waking"}
)
ALLOWED_INTENTS: Final[frozenset[str]] = frozenset(
    {
        "stay_asleep",
        "wake_up",
        "soft_reunion",
        "seek_attention",
        "play",
        "soothe",
        "settle",
        "celebrate",
        "comfort_self",
        "alert_owner",
    }
)
PROPOSAL_FIELDS: Final[frozenset[str]] = ALLOWED_FIELDS | {"intent", "body_state"}


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


@dataclass(frozen=True)
class BehaviorProposal:
    """A host-only model proposal: a device behavior plus optional continuity hints.

    ``intent`` and ``body_state`` are advisory fields the host uses to drive body
    continuity. They are validated here but never sent to the device;
    ``to_behavior_command`` returns the strict frame the firmware expects.
    """

    behavior: BehaviorCommand
    intent: str | None = None
    body_state: str | None = None

    def to_behavior_command(self) -> BehaviorCommand:
        return self.behavior


def parse_behavior_response(response_text: str) -> BehaviorCommand:
    raw = _load_behavior_object(response_text)
    command, _intent, _body = _validate_behavior_object(raw, allow_proposal=False)
    return command


def parse_behavior_proposal(response_text: str) -> BehaviorProposal:
    raw = _load_behavior_object(response_text)
    command, intent, body_state = _validate_behavior_object(raw, allow_proposal=True)
    return BehaviorProposal(behavior=command, intent=intent, body_state=body_state)


def _validate_behavior_object(
    raw: object, *, allow_proposal: bool
) -> tuple[BehaviorCommand, str | None, str | None]:
    if not isinstance(raw, dict):
        raise ValidationError("model output must be a single JSON object")

    allowed_fields = PROPOSAL_FIELDS if allow_proposal else ALLOWED_FIELDS
    unexpected = set(raw) - allowed_fields
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
    if mood not in ALLOWED_MOODS:
        raise ValidationError(f"unknown mood {mood!r}")
    mood = ANIMATION_TO_MOOD[animation]

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

    intent: str | None = None
    body_state: str | None = None
    if allow_proposal:
        # An unknown intent is rejected; an unknown body_state is softened to
        # "no proposal" so the body model falls back to its deterministic default
        # rather than discarding an otherwise-valid behavior frame.
        intent = _optional_proposal_field(raw, "intent", ALLOWED_INTENTS, soften=False)
        body_state = _optional_proposal_field(
            raw, "body_state", ALLOWED_BODY_STATES, soften=True
        )

    command = BehaviorCommand(
        mood=mood,
        animation=animation,
        text=text,
        alert=alert,
        duration_ms=duration_ms,
    )
    return command, intent, body_state


def _optional_proposal_field(
    raw: dict[str, object], field: str, allowed: frozenset[str], *, soften: bool
) -> str | None:
    value = raw.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string when present")
    cleaned = value.strip().lower()
    if not cleaned:
        return None
    if cleaned not in allowed:
        if soften:
            return None
        raise ValidationError(f"unknown {field} {value!r}")
    return cleaned


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
