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
    playfulness: int = 55
    sleepiness: int = 25
    quiet_cycles: int = 0
    last_event: str = "host loop started"

    def prompt_context(self) -> str:
        return (
            "Persistent pet state:\n"
            f"- mood: {self.mood}\n"
            f"- energy: {self.energy}/100\n"
            f"- attention: {self.attention}/100\n"
            f"- affection: {self.affection}/100\n"
            f"- playfulness: {self.playfulness}/100\n"
            f"- sleepiness: {self.sleepiness}/100\n"
            f"- quiet_cycles: {self.quiet_cycles}\n"
            f"- last_event: {self.last_event}"
        )

    def observe(self, behavior: BehaviorCommand, event: str) -> None:
        self.mood = behavior.mood
        self.last_event = _short_event(event)
        is_quiet = event.lower().startswith("quiet desk time")
        self.quiet_cycles = self.quiet_cycles + 1 if is_quiet else 0

        match behavior.animation:
            case "happy" | "excited":
                self.energy += 8
                self.attention += 4
                self.affection += 4
                self.playfulness -= 8
                self.sleepiness -= 6
            case "play":
                self.energy -= 4
                self.attention += 8
                self.affection += 5
                self.playfulness -= 14
                self.sleepiness += 2
            case "sleepy" | "nap":
                self.energy -= 10
                self.attention -= 4
                self.playfulness += 4
                self.sleepiness += 12
            case "worried":
                self.energy -= 6
                self.attention += 10
                self.playfulness -= 6
                self.sleepiness += 3
            case "alert":
                self.energy += 10
                self.attention += 18
                self.playfulness -= 8
                self.sleepiness -= 10
            case "look_around" | "walk":
                self.attention += 6
                self.playfulness += 3
                self.energy -= 2
            case "blink" | "idle":
                self.energy -= 1
                self.attention -= 2
                self.playfulness += 2
                self.sleepiness += 2

        self.clamp()

    def idle_event(self) -> str:
        if self.sleepiness >= 75:
            return "Quiet desk time. Mochi is getting drowsy and may choose a real nap."
        if self.energy >= 70 and self.playfulness >= 60:
            return "Quiet desk time. Mochi feels playful and may invent a tiny game."
        if self.quiet_cycles >= 3 and self.playfulness >= 50:
            return "Quiet desk time. Mochi is bored and may wander, play, or demand attention."
        if self.attention <= 25:
            return "Quiet desk time. Mochi has not had attention lately and may act needy."
        if self.quiet_cycles % 4 == 2:
            return "Quiet desk time. Mochi may patrol the cube or sniff around."
        return "Quiet desk time. Nothing urgent is happening, but Mochi can choose a small self-directed action."

    def clamp(self) -> None:
        self.energy = _clamp_stat(self.energy)
        self.attention = _clamp_stat(self.attention)
        self.affection = _clamp_stat(self.affection)
        self.playfulness = _clamp_stat(self.playfulness)
        self.sleepiness = _clamp_stat(self.sleepiness)


def build_stateful_prompt(state: PetState, event: str) -> str:
    cleaned = event.strip()
    if not cleaned:
        raise ValueError("event must not be empty")
    return (
        f"{state.prompt_context()}\n\n"
        f"Current moment:\n{cleaned}\n\n"
        "Choose Mochi's next behavior for the next few seconds.\n"
        "Quiet desk time should feel like Mochi is a real pet being driven by "
        "the model: choose self-directed actions sometimes, including walk, "
        "play, excited, or nap when the state supports it. Do not wait for "
        "message/build/test/alert inputs to be expressive. Use idle, blink, "
        "and look_around only as calm beats between bigger actions, usually "
        "with empty text. Use nap when Mochi is truly sleepy and sleepy when "
        "only drowsy. Use happy/excited/play for direct affection, praise, "
        "passed build/test results, or spontaneous joyful energy. "
        "Use worried for failed build/test results and confusing trouble. "
        "Use alert only for important alerts that need the human to look now. "
        "Keep text to one or two short ASCII words, and prefer no text for "
        "idle-capable animations. Prefer duration_ms 2500-5000 for normal "
        "reactions, 1000-2500 for blink/look_around, 3000-7000 for walk/play/"
        "excited, and 5000-9000 for sleepy, nap, or alert."
    )


def _clamp_stat(value: int) -> int:
    return min(MAX_STAT, max(MIN_STAT, value))


def _short_event(event: str) -> str:
    cleaned = " ".join(event.split())
    return cleaned[:EVENT_MAX_LEN] if cleaned else "quiet desk time"
