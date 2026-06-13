from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from host.prompt import (
    build_situation_prompt,
    extract_pet_name,
    load_personality_prompt,
    load_pet_name,
)


class PromptTests(unittest.TestCase):
    def test_load_pet_name_uses_pet_md_identity(self) -> None:
        self.assertEqual(load_pet_name(), "Mochi")

    def test_extract_pet_name_reads_identity_name(self) -> None:
        self.assertEqual(extract_pet_name("- **Name:** Pip\n"), "Pip")

    def test_personality_prompt_includes_configured_pet_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pet_path = Path(temp_dir) / "PET.md"
            pet_path.write_text(
                "\n".join(
                    (
                        "# Pet",
                        "",
                        "## Identity",
                        "",
                        "- **Name:** Pip",
                        "",
                        "## Personality prompt (LLM system prompt)",
                        "",
                        "```text",
                        "You are a tiny desk pet.",
                        "```",
                    )
                ),
                encoding="utf-8",
            )

            prompt = load_personality_prompt(pet_path)

        self.assertIn("Pet name: Pip", prompt)
        self.assertIn("You are a tiny desk pet.", prompt)

    def test_situation_prompt_uses_configured_pet_name(self) -> None:
        prompt = build_situation_prompt("the build passed", pet_name="Pip")

        self.assertIn("Current moment for Pip:", prompt)
        self.assertIn("the build passed", prompt)


if __name__ == "__main__":
    unittest.main()
