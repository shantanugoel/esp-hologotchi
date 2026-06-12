from __future__ import annotations

import unittest

from host.memory import KIND_SEMANTIC, MemoryStore
from host.reflection import consolidate_memory


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class ReflectionTests(unittest.TestCase):
    def test_repeated_observed_praise_consolidates_stable_fact_once(self) -> None:
        clock = FakeClock()
        store = MemoryStore(now=clock)
        self.addCleanup(store.close)

        for index in range(3):
            store.capture(
                "direct_message",
                f"owner praised Mochi {index}",
                valence=70,
                intensity=60,
                owner_initiated=True,
                tags=["message", "praise"],
            )
            clock.advance(10)

        self.assertEqual(consolidate_memory(store), 1)
        self.assertEqual(consolidate_memory(store), 0)

        facts = store.retrieve(tags=["preference"], limit=5)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].kind, KIND_SEMANTIC)
        self.assertIn("praises Mochi", facts[0].summary)

    def test_consolidation_waits_for_threshold(self) -> None:
        store = MemoryStore()
        self.addCleanup(store.close)
        store.capture(
            "direct_message",
            "owner praised Mochi once",
            valence=70,
            intensity=60,
            owner_initiated=True,
            tags=["message", "praise"],
        )

        self.assertEqual(consolidate_memory(store), 0)


if __name__ == "__main__":
    unittest.main()
