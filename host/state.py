from __future__ import annotations

from dataclasses import dataclass, field

from .affect import (
    GRUMPY,
    NEEDY,
    RESTLESS,
    SAD,
    WITHDRAWN,
    Affect,
)
from .presence import PresenceReport
from .protocol import BehaviorCommand

EVENT_MAX_LEN = 80
RECENT_PHRASE_LIMIT = 5
RECENT_ANIMATION_LIMIT = 5

PRAISE_WORDS = frozenset(
    {
        "good", "love", "nice", "great", "proud", "yay", "best", "thanks",
        "thank", "awesome", "clever", "cute", "amazing", "yes", "win",
    }
)
HARSH_WORDS = frozenset(
    {"bad", "stop", "no", "quiet", "shut", "stupid", "annoying", "hate", "ugh", "dumb"}
)
APOLOGY_WORDS = frozenset({"sorry", "apology", "apologies", "apologize", "forgive", "oops"})

MESSAGE_PRAISE = "praise"
MESSAGE_HARSH = "harsh"
MESSAGE_APOLOGY = "apology"
MESSAGE_NEUTRAL = "neutral"

def _guidance(pet_name: str) -> str:
    return (
        f"Choose {pet_name}'s next behavior for the next few seconds, in character.\n"
        f"{pet_name} is driven by its inner state above, not by waiting for inputs. "
        "During quiet desk time it still makes self-directed choices: walk, "
        "look_around, play, excited, or nap when the state supports it. Use idle "
        "and blink as calm beats, usually with empty text.\n"
        "Express feelings through the existing behaviors (the device has no new "
        "animations): sad or withdrawn -> worried, sleepy, nap, or idle; grumpy -> "
        "worried, look_around, or walk; needy -> play, look_around, or happy; bright "
        "and bonded -> happy, play, or excited. Use nap when truly sleepy and sleepy "
        "when only drowsy.\n"
        "Use happy/excited/play for direct affection, praise, passed build/test "
        "results, returning after an absence, or spontaneous joy. Use worried for "
        "failed build/test results and trouble. Use alert only for important alerts "
        "that need the human now. If the current moment starts with 'Important "
        "alert:', animation must be alert and alert must be true.\n"
        "When you include text, keep it varied, dog-like, and one to three short "
        "ASCII words; avoid repeating recent phrases. Avoid repeating recent "
        "animations when another listed behavior fits. Prefer no text for idle, blink, "
        "and look_around. A self-made attention alert is only allowed when the current "
        "moment explicitly says Self-made attention alert; otherwise reserve alert for "
        "important alerts. Prefer duration_ms 2500-5000 for normal reactions, "
        "1000-2500 for blink/look_around, 6500-10000 for walk, 3000-7000 for "
        "play/excited, and 5000-9000 for sleepy, nap, or alert."
    )


@dataclass
class PetState:
    affect: Affect = field(default_factory=Affect)
    mood: str = "calm"
    last_event: str = "host loop started"
    recent_phrases: tuple[str, ...] = ()
    recent_animations: tuple[str, ...] = ()
    green_build_total: int = 0
    green_build_streak: int = 0
    failure_streak: int = 0
    last_interaction_day: str = ""
    last_daybeat_key: str = ""
    last_callback_at: float = 0.0
    last_self_nudge_at: float = 0.0

    def prompt_context(self) -> str:
        return (
            f"{self.affect.prompt_block()}\n\n"
            f"Recent phrases: {_format_recent_phrases(self.recent_phrases)}\n"
            f"Recent animations: {_format_recent_animations(self.recent_animations)}\n"
            f"last_event: {self.last_event}"
        )

    def observe(self, behavior: BehaviorCommand, event: str) -> None:
        self.mood = behavior.mood
        self.last_event = _short_event(event)
        if behavior.text:
            self.recent_phrases = (*self.recent_phrases, behavior.text)[-RECENT_PHRASE_LIMIT:]
        self.recent_animations = (
            *self.recent_animations,
            behavior.animation,
        )[-RECENT_ANIMATION_LIMIT:]
        self.affect.register_behavior(behavior.animation)


def build_stateful_prompt(
    state: PetState,
    event: str,
    *,
    pet_name: str = "Mochi",
    presence: PresenceReport | None = None,
    memories: tuple[str, ...] = (),
) -> str:
    cleaned = event.strip()
    if not cleaned:
        raise ValueError("event must not be empty")

    parts: list[str] = []
    if memories:
        parts.append("Relevant memories:\n" + "\n".join(f"- {memory}" for memory in memories))
    parts.append(state.prompt_context())
    if presence is not None:
        parts.append(_presence_block(presence))
    parts.append(f"Current moment:\n{cleaned}")
    parts.append(_guidance(pet_name))
    return "\n\n".join(parts)


def describe_self_directed_situation(
    state: PetState, presence: PresenceReport, *, pet_name: str = "Mochi"
) -> str:
    affect = state.affect
    inner = affect.overall_state()

    if presence.returned_from_away:
        return (
            "The human just came back after being away for a while. React like a "
            "pet greeting its owner."
        )
    if presence.away:
        return f"Quiet desk time. The human seems away; {pet_name} can wait calmly or nap."
    if affect.is_sleepy():
        return f"Quiet desk time. {pet_name} is getting drowsy and may settle toward a nap."
    if inner == WITHDRAWN:
        return (
            f"Quiet desk time. {pet_name} has been neglected and feels withdrawn - a "
            "low-energy sulk that attention, play, or an apology could still win back."
        )
    if inner == GRUMPY:
        return f"Quiet desk time. {pet_name} is frustrated and a little grumpy."
    if inner == SAD:
        return (
            f"Quiet desk time. {pet_name} feels lonely after being ignored and wants "
            "attention."
        )
    if presence.focus_pressure > 0 and presence.focus_app:
        return (
            "Quiet desk time. The human has been heads-down on one thing for a long "
            f"while; {pet_name} feels a little jealous and wants to be noticed."
        )
    if inner == NEEDY:
        return (
            f"Quiet desk time. {pet_name} wants attention and may angle for a game or a "
            "look."
        )
    if inner == RESTLESS:
        return (
            f"Quiet desk time. {pet_name} is restless and may patrol, sniff, or invent a "
            "tiny game."
        )
    if affect.is_bright():
        return f"Quiet desk time. {pet_name} feels good and playful and may start a little game."
    return (
        f"Quiet desk time. Nothing urgent is happening, but {pet_name} can choose a small "
        "self-directed action."
    )


def classify_message(text: str) -> str:
    words = {word.strip(".,!?\"'").lower() for word in text.split()}
    if words & APOLOGY_WORDS:
        return MESSAGE_APOLOGY
    if words & HARSH_WORDS:
        return MESSAGE_HARSH
    if words & PRAISE_WORDS:
        return MESSAGE_PRAISE
    return MESSAGE_NEUTRAL


def _presence_block(presence: PresenceReport) -> str:
    line = (
        f"Presence: {presence.state.value} "
        f"(ignored {int(presence.ignored_seconds)}s, away {int(presence.away_seconds)}s)."
    )
    if presence.focus_pressure > 0 and presence.focus_app:
        line += f" The human has been heads-down in {presence.focus_app} for a long while."
    return line


def _short_event(event: str) -> str:
    cleaned = " ".join(event.split())
    return cleaned[:EVENT_MAX_LEN] if cleaned else "quiet desk time"


def _format_recent_phrases(phrases: tuple[str, ...]) -> str:
    return ", ".join(phrases) if phrases else "none"


def _format_recent_animations(animations: tuple[str, ...]) -> str:
    return ", ".join(animations) if animations else "none"
