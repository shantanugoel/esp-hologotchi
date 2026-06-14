from __future__ import annotations

import os
import tempfile
import threading
import unittest

from host.memory import (
    IMPORTANCE_THRESHOLD,
    KIND_SEMANTIC,
    SCHEMA_VERSION,
    MemoryStore,
)


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class MemoryCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.store = MemoryStore(now=self.clock)
        self.addCleanup(self.store.close)

    def test_salient_owner_moment_is_stored(self) -> None:
        memory_id = self.store.capture(
            "direct_message",
            "owner praised Shiro after tests passed",
            valence=70,
            intensity=60,
            owner_initiated=True,
            tags=["praise", "tests"],
        )

        self.assertIsNotNone(memory_id)
        self.assertEqual(self.store.count(), 1)

    def test_low_salience_idle_moment_is_dropped(self) -> None:
        memory_id = self.store.capture(
            "idle",
            "nothing much happened on the quiet desk",
            valence=0,
            intensity=5,
        )

        self.assertIsNone(memory_id)
        self.assertEqual(self.store.count(), 0)

    def test_duplicate_within_window_bumps_instead_of_inserting(self) -> None:
        first = self.store.capture(
            "test_result", "Test failed.", valence=-60, intensity=70
        )
        self.clock.advance(60)
        second = self.store.capture(
            "test_result", "Test failed.", valence=-60, intensity=70
        )

        self.assertEqual(first, second)
        self.assertEqual(self.store.count(), 1)
        record = self.store.recent()[0]
        self.assertEqual(record.recall_count, 1)

    def test_capture_rejects_unknown_kind(self) -> None:
        with self.assertRaises(ValueError):
            self.store.capture("x", "y", kind="mystery")


class MemoryRetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.store = MemoryStore(now=self.clock)
        self.addCleanup(self.store.close)

    def test_retrieval_prefers_text_match(self) -> None:
        self.store.capture(
            "test_result", "builds failed twice in a row", valence=-70, intensity=80,
            tags=["build"],
        )
        self.clock.advance(10)
        self.store.capture(
            "direct_message", "owner said good pup", valence=60, intensity=50,
            owner_initiated=True, tags=["praise"],
        )

        results = self.store.retrieve(query="build failures", limit=1)

        self.assertEqual(len(results), 1)
        self.assertIn("build", results[0].summary)

    def test_retrieval_filters_by_tag(self) -> None:
        self.store.capture(
            "direct_message", "owner praised the pup", valence=60, intensity=50,
            owner_initiated=True, tags=["praise"],
        )
        self.store.capture(
            "test_result", "tests failed badly", valence=-60, intensity=70,
            tags=["build"],
        )

        results = self.store.retrieve(tags=["praise"], limit=5)

        self.assertTrue(results)
        self.assertTrue(all("praise" in record.tags for record in results))

    def test_recall_bumps_importance(self) -> None:
        memory_id = self.store.capture(
            "alert", "important alert was raised", valence=40, intensity=80, alert=True
        )
        assert memory_id is not None
        before = self.store.recent()[0].importance

        self.store.retrieve(query="important alert", limit=5)

        after = self.store.recent()[0].importance
        self.assertGreater(after, before)

    def test_importance_decays_with_age(self) -> None:
        self.store.capture("alert", "old alert", valence=50, intensity=90, alert=True)
        self.clock.advance(10)
        self.store.capture("alert", "fresh alert now", valence=50, intensity=90, alert=True)

        # Age the first far into the future so decay dominates ranking.
        self.clock.advance(30 * 24 * 3600)
        results = self.store.retrieve(limit=1)

        self.assertEqual(results[0].summary, "fresh alert now")

    def test_spontaneous_callback_prefers_old_meaningful_memory(self) -> None:
        self.store.capture(
            "direct_message",
            "owner said good pup yesterday",
            valence=70,
            intensity=60,
            owner_initiated=True,
            tags=["praise"],
        )
        self.clock.advance(2 * 24 * 3600)

        record = self.store.spontaneous_callback()

        self.assertIsNotNone(record)
        assert record is not None
        self.assertIn("good pup", record.summary)

    def test_spontaneous_callback_respects_age(self) -> None:
        self.store.capture(
            "direct_message",
            "owner said good pup just now",
            valence=70,
            intensity=60,
            owner_initiated=True,
            tags=["praise"],
        )

        self.assertIsNone(self.store.spontaneous_callback())


class MemoryControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.store = MemoryStore(now=self.clock)
        self.addCleanup(self.store.close)

    def test_forget_single_memory(self) -> None:
        memory_id = self.store.capture("alert", "an alert", valence=50, intensity=80, alert=True)
        assert memory_id is not None

        removed = self.store.forget(memory_id)

        self.assertEqual(removed, 1)
        self.assertEqual(self.store.count(), 0)

    def test_forget_by_source(self) -> None:
        self.store.capture("alert", "alert one", valence=50, intensity=80, alert=True)
        self.clock.advance(10)
        self.store.capture("test_result", "tests failed", valence=-60, intensity=70)

        removed = self.store.forget_by(source="alert")

        self.assertEqual(removed, 1)
        self.assertEqual(self.store.count(), 1)

    def test_forget_by_requires_a_filter(self) -> None:
        with self.assertRaises(ValueError):
            self.store.forget_by()

    def test_reset_clears_memories_and_state(self) -> None:
        self.store.capture("alert", "alert", valence=50, intensity=80, alert=True)
        self.store.save_affect({"social": 10.0}, last_interaction=123.0)

        self.store.reset()

        self.assertEqual(self.store.count(), 0)
        self.assertIsNone(self.store.load_affect().affect)

    def test_disabled_writes_drop_captures(self) -> None:
        self.store.set_writes_enabled(False)

        memory_id = self.store.capture("alert", "alert", valence=50, intensity=80, alert=True)

        self.assertIsNone(memory_id)
        self.assertEqual(self.store.count(), 0)
        self.assertFalse(self.store.summary().writes_enabled)


class MemoryPersistenceTests(unittest.TestCase):
    def test_memory_survives_reopen(self) -> None:
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "memory.db")
            store = MemoryStore(path, now=clock)
            store.capture(
                "direct_message", "owner praised Shiro", valence=70, intensity=60,
                owner_initiated=True,
            )
            store.save_affect({"social": 42.0, "bond": 15.0}, last_interaction=999.0)
            store.close()

            reopened = MemoryStore(path, now=clock)
            self.addCleanup(reopened.close)
            self.assertEqual(reopened.count(), 1)
            stored = reopened.load_affect()
            self.assertEqual(stored.affect["social"], 42.0)
            self.assertEqual(stored.last_interaction, 999.0)

    def test_fts_search_still_works_after_reopen(self) -> None:
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "memory.db")
            store = MemoryStore(path, now=clock)
            self.assertTrue(store._fts)
            store.capture(
                "test_result", "builds failing repeatedly", valence=-70, intensity=80,
                tags=["build"],
            )
            store.close()

            reopened = MemoryStore(path, now=clock)
            self.addCleanup(reopened.close)
            # FTS must be detected on reopen, not silently degraded to LIKE.
            self.assertTrue(reopened._fts)
            # Prefix match ("build" -> "builds") only works via the FTS path.
            results = reopened.retrieve(query="build", limit=1)
            self.assertTrue(results)
            self.assertIn("build", results[0].summary)

    def test_schema_version_is_recorded(self) -> None:
        store = MemoryStore(now=FakeClock())
        self.addCleanup(store.close)
        version = store._conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, SCHEMA_VERSION)


class MemoryThreadSafetyTests(unittest.TestCase):
    def test_concurrent_captures_do_not_corrupt(self) -> None:
        store = MemoryStore(now=FakeClock())
        self.addCleanup(store.close)

        def worker(worker_id: int) -> None:
            for index in range(25):
                store.capture(
                    f"src-{worker_id}",
                    f"event {worker_id}-{index}",
                    valence=80,
                    intensity=80,
                    owner_initiated=True,
                )

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(store.count(), 100)


class MemorySalienceConstantTests(unittest.TestCase):
    def test_threshold_is_reasonable(self) -> None:
        self.assertGreater(IMPORTANCE_THRESHOLD, 0)
        self.assertLess(IMPORTANCE_THRESHOLD, 100)

    def test_semantic_kind_is_accepted(self) -> None:
        store = MemoryStore(now=FakeClock())
        self.addCleanup(store.close)
        memory_id = store.capture(
            "reflection", "owner often says good pup", kind=KIND_SEMANTIC,
            valence=50, intensity=60, owner_initiated=True,
        )
        self.assertIsNotNone(memory_id)


if __name__ == "__main__":
    unittest.main()


class MemoryRecallCooldownTests(unittest.TestCase):
    def test_repeated_recalls_in_cooldown_do_not_keep_inflating(self) -> None:
        clock = FakeClock()
        store = MemoryStore(now=clock)
        self.addCleanup(store.close)
        store.capture("alert", "calendar soon", valence=50, intensity=80, alert=True)

        store.retrieve(query="unrelated", limit=3)
        after_first = store.recent()[0].importance
        for _ in range(10):
            store.retrieve(query="unrelated", limit=3)
        after_many = store.recent()[0].importance

        self.assertEqual(after_first, after_many)

    def test_recall_after_cooldown_bumps_again(self) -> None:
        clock = FakeClock()
        store = MemoryStore(now=clock)
        self.addCleanup(store.close)
        store.capture("alert", "calendar soon", valence=50, intensity=80, alert=True)

        store.retrieve(limit=3)
        first = store.recent()[0].importance
        clock.advance(600)
        store.retrieve(limit=3)
        later = store.recent()[0].importance

        self.assertGreater(later, first)
