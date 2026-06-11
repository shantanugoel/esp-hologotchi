from __future__ import annotations

from dataclasses import dataclass

from .protocol import BehaviorCommand

MIN_STAT = 0
MAX_STAT = 100
EVENT_MAX_LEN = 80


@dataclass
class PetState:
    mood: str = "calm"
    energy: int = 55
    attention: int = 50
    affection: int = 70
    sleepiness: int = 25
    last_event: str = "host loop started"

    def prompt_context(self) -> str:
        return (
            "Persistent pet state:\n"
            f"- mood: {self.mood}\n"
            f"- energy: {self.energy}/100\n"
            f"- attention: {self.attention}/100\n"
            f"- affection: {self.affection}/100\n"
            f"- sleepiness: {self.sleepiness}/100\n"
            f"- last_event: {self.last_event}"
        )

    def observe(self, behavior: BehaviorCommand, event: str) -> None:
        self.mood = behavior.mood
        self.last_event = _short_event(event)

        match behavior.animation:
            case "happy":
                self.energy += 8
                self.attention += 4
                self.affection += 4
                self.sleepiness -= 6
            case "sleepy":
                self.energy -= 10
                self.attention -= 4
                self.sleepiness += 12
            case "worried":
                self.energy -= 6
                self.attention += 10
                self.sleepiness += 3
            case "alert":
                self.energy += 10
                self.attention += 18
                self.sleepiness -= 10
            case "look_around":
                self.attention += 6
                self.energy -= 1
            case "blink" | "idle":
                self.energy -= 1
                self.attention -= 2
                self.sleepiness += 2

        self.clamp()

    def idle_event(self) -> str:
        if self.sleepiness >= 75:
            return "Quiet desk time. Mochi is getting drowsy."
        if self.attention <= 25:
            return "Quiet desk time. Mochi has not had attention lately."
        return "Quiet desk time. Nothing urgent is happening."

    def clamp(self) -> None:
        self.energy = _clamp_stat(self.energy)
        self.attention = _clamp_stat(self.attention)
        self.affection = _clamp_stat(self.affection)
        self.sleepiness = _clamp_stat(self.sleepiness)


def build_stateful_prompt(state: PetState, event: str) -> str:
    cleaned = event.strip()
    if not cleaned:
        raise ValueError("event must not be empty")
    return (
        f"{state.prompt_context()}\n\n"
        f"Current moment:\n{cleaned}\n\n"
        "Choose Mochi's next behavior for the next few seconds.\n"
        "Quiet desk time should still feel alive: vary between idle, blink, "
        "and look_around instead of choosing idle every cycle, usually with "
        "empty text. Use sleepy only when Mochi is drowsy or winding down. "
        "Use happy for direct affection, praise, and passed build/test results. "
        "Use worried for failed build/test results and confusing trouble. "
        "Use alert only for important alerts that need the human to look now. "
        "Keep text to one or two short ASCII words, and prefer no text for "
        "idle-capable animations. Prefer duration_ms 2500-5000 for normal "
        "reactions, 1000-2500 for blink/look_around, and 5000-9000 for sleepy "
        "or alert."
    )


def _clamp_stat(value: int) -> int:
    return min(MAX_STAT, max(MIN_STAT, value))


def _short_event(event: str) -> str:
    cleaned = " ".join(event.split())
    return cleaned[:EVENT_MAX_LEN] if cleaned else "quiet desk time"
