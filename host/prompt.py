from __future__ import annotations

import re
from pathlib import Path
from typing import Final

NAME_RE: Final = re.compile(r"^- \*\*Name:\*\*\s*(?P<name>.+?)\s*$", re.MULTILINE)
PROMPT_HEADING: Final = "## Personality prompt (LLM system prompt)"
PROMPT_BLOCK_RE: Final = re.compile(r"```text\n(.*?)\n```", re.DOTALL)
REPO_ROOT: Final = Path(__file__).resolve().parents[1]


def load_pet_name(pet_path: Path | None = None) -> str:
    pet_path = pet_path or (REPO_ROOT / "PET.md")
    markdown = pet_path.read_text(encoding="utf-8")
    return extract_pet_name(markdown)


def load_personality_prompt(pet_path: Path | None = None) -> str:
    pet_path = pet_path or (REPO_ROOT / "PET.md")
    markdown = pet_path.read_text(encoding="utf-8")
    pet_name = extract_pet_name(markdown)
    personality = extract_personality_prompt(markdown)
    return f"Pet name: {pet_name}\n\n{personality}"


def extract_pet_name(markdown: str) -> str:
    match = NAME_RE.search(markdown)
    if match is None:
        raise RuntimeError("could not find '- **Name:** ...' in PET.md")

    name = match.group("name").strip()
    if not name:
        raise RuntimeError("pet name in PET.md must not be empty")
    return name


def extract_personality_prompt(markdown: str) -> str:
    heading_index = markdown.find(PROMPT_HEADING)
    if heading_index == -1:
        raise RuntimeError(f"could not find {PROMPT_HEADING!r} in PET.md")

    match = PROMPT_BLOCK_RE.search(markdown, heading_index)
    if match is None:
        raise RuntimeError("could not find the fenced personality prompt block in PET.md")

    return match.group(1).strip()


def build_situation_prompt(user_prompt: str, *, pet_name: str) -> str:
    cleaned = user_prompt.strip()
    if not cleaned:
        raise ValueError("prompt must not be empty")
    return (
        f"Current moment for {pet_name}:\n{cleaned}\n\n"
        "Reply with the single JSON behavior update only."
    )
