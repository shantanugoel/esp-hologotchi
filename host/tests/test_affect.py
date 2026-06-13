from __future__ import annotations

import unittest

from host.affect import (
    CONTENT,
    GRUMPY,
    NEEDY,
    SAD,
    WITHDRAWN,
    Affect,
)


class AffectDecayTests(unittest.TestCase):
    def test_first_advance_only_sets_marker(self) -> None:
        affect = Affect(social=80.0)

        affect.advance(now=1000.0)

        self.assertEqual(affect.last_update, 1000.0)
        self.assertEqual(affect.snapshot()["social"], 80)

    def test_drives_decay_over_real_elapsed_time(self) -> None:
        affect = Affect(social=80.0, play=80.0, stimulation=80.0, last_update=1000.0)

        affect.advance(now=1000.0 + 600.0)  # ten minutes

        snap = affect.snapshot()
        self.assertLess(snap["social"], 80)
        self.assertLess(snap["play"], 80)
        self.assertLess(snap["stimulation"], 80)
        self.assertEqual(affect.last_update, 1600.0)

    def test_decay_is_cadence_independent(self) -> None:
        once = Affect(social=90.0, last_update=0.0)
        once.advance(now=1.0)  # initialize marker
        stepped = Affect(social=90.0, last_update=0.0)
        stepped.advance(now=1.0)

        once.advance(now=1.0 + 600.0)
        for step in range(10):
            stepped.advance(now=1.0 + 60.0 * (step + 1))

        self.assertAlmostEqual(once.social, stepped.social, places=6)

    def test_ignoring_grows_loneliness_faster_than_quiet_presence(self) -> None:
        quiet = Affect(social=20.0, loneliness=20.0, last_update=1000.0)
        ignored = Affect(social=20.0, loneliness=20.0, last_update=1000.0)

        quiet.advance(now=1600.0, ignoring=False)
        ignored.advance(now=1600.0, ignoring=True)

        self.assertGreater(ignored.loneliness, quiet.loneliness)

    def test_away_does_not_grow_loneliness(self) -> None:
        affect = Affect(social=10.0, loneliness=30.0, last_update=1000.0)

        affect.advance(now=1000.0 + 3600.0, away=True)

        self.assertLessEqual(affect.loneliness, 30)
        # Social pressure pauses while genuinely away.
        self.assertEqual(affect.snapshot()["social"], 10)

    def test_focus_pressure_adds_jealous_frustration_when_ignoring(self) -> None:
        calm = Affect(social=30.0, frustration=10.0, last_update=1000.0)
        jealous = Affect(social=30.0, frustration=10.0, last_update=1000.0)

        calm.advance(now=1600.0, ignoring=True, focus_pressure=0.0)
        jealous.advance(now=1600.0, ignoring=True, focus_pressure=1.0)

        self.assertGreater(jealous.frustration, calm.frustration)
        self.assertGreater(jealous.loneliness, calm.loneliness)

    def test_clock_going_backwards_is_ignored(self) -> None:
        affect = Affect(social=50.0, last_update=2000.0)

        affect.advance(now=1000.0)

        self.assertEqual(affect.snapshot()["social"], 50)
        self.assertEqual(affect.last_update, 1000.0)


class AffectEventTests(unittest.TestCase):
    def test_attention_replenishes_social_and_eases_loneliness(self) -> None:
        affect = Affect(social=20.0, loneliness=70.0)

        affect.register_attention()

        self.assertGreater(affect.social, 20)
        self.assertLess(affect.loneliness, 70)

    def test_apology_repairs_frustration(self) -> None:
        affect = Affect(frustration=80.0)

        affect.register_apology()

        self.assertLess(affect.frustration, 80)

    def test_repeated_bad_outcomes_raise_frustration(self) -> None:
        affect = Affect(frustration=10.0)

        affect.register_bad_outcome()
        affect.register_bad_outcome()

        self.assertGreaterEqual(affect.frustration, 40)

    def test_nap_behavior_restores_rest_and_energy(self) -> None:
        affect = Affect(rest=10.0, sleepiness=90.0, energy=20.0)

        affect.register_behavior("nap")

        self.assertGreater(affect.rest, 10)
        self.assertLess(affect.sleepiness, 90)
        self.assertGreater(affect.energy, 20)

    def test_stats_stay_bounded(self) -> None:
        affect = Affect(social=99.0)
        for _ in range(20):
            affect.register_attention()
        self.assertLessEqual(affect.social, 100)
        self.assertEqual(affect.snapshot()["social"], 100)


class AffectTouchTests(unittest.TestCase):
    def test_boop_gives_light_attention_and_pep(self) -> None:
        affect = Affect(social=20.0, stimulation=20.0, energy=40.0, loneliness=60.0)

        affect.register_boop()

        self.assertGreater(affect.social, 20)
        self.assertGreater(affect.stimulation, 20)
        self.assertGreater(affect.energy, 40)
        self.assertLess(affect.loneliness, 60)

    def test_pet_soothes_and_repairs_affection(self) -> None:
        affect = Affect(
            affection=40.0, frustration=80.0, loneliness=70.0, stimulation=50.0
        )

        affect.register_pet()

        self.assertGreater(affect.affection, 40)
        self.assertLess(affect.frustration, 80)
        self.assertLess(affect.loneliness, 70)
        # A hold meets the contact need; it must not lower a satisfaction drive.
        self.assertGreaterEqual(affect.stimulation, 50)

    def test_repeated_petting_does_not_make_a_content_pet_restless(self) -> None:
        from host.affect import CONTENT, RESTLESS

        affect = Affect(stimulation=40.0, play=60.0)
        self.assertEqual(affect.overall_state(), CONTENT)

        for _ in range(5):
            affect.register_pet()

        self.assertNotEqual(affect.overall_state(), RESTLESS)
        self.assertGreaterEqual(affect.stimulation, 40)

    def test_pet_does_not_force_play(self) -> None:
        affect = Affect(play=30.0)

        affect.register_pet()

        self.assertEqual(affect.play, 30.0)

    def test_play_invite_replenishes_play_and_stimulation(self) -> None:
        affect = Affect(play=20.0, stimulation=20.0, energy=80.0)

        affect.register_play_invite()

        self.assertGreater(affect.play, 20)
        self.assertGreater(affect.stimulation, 20)
        # Play costs a little energy.
        self.assertLess(affect.energy, 80)


class AffectStateTests(unittest.TestCase):
    def test_content_when_drives_are_healthy(self) -> None:
        affect = Affect(
            social=70, play=70, rest=70, stimulation=70, energy=70,
            loneliness=10, frustration=10,
        )
        self.assertEqual(affect.overall_state(), CONTENT)

    def test_needy_when_social_low(self) -> None:
        affect = Affect(social=20, play=60, loneliness=45, frustration=10, energy=60)
        self.assertEqual(affect.overall_state(), NEEDY)

    def test_sad_when_lonely(self) -> None:
        affect = Affect(social=15, loneliness=70, frustration=10, energy=50)
        self.assertEqual(affect.overall_state(), SAD)

    def test_grumpy_when_frustrated(self) -> None:
        affect = Affect(frustration=75, loneliness=10, energy=60)
        self.assertEqual(affect.overall_state(), GRUMPY)

    def test_withdrawn_when_low_energy_and_distressed(self) -> None:
        affect = Affect(energy=15, loneliness=70, frustration=20)
        self.assertEqual(affect.overall_state(), WITHDRAWN)

    def test_suggested_animations_map_to_known_vocabulary(self) -> None:
        from host.protocol import ANIMATION_TO_MOOD

        affect = Affect(social=15, loneliness=70, frustration=10, energy=50)
        for animation in affect.suggested_animations():
            self.assertIn(animation, ANIMATION_TO_MOOD)

    def test_prompt_block_contains_relationship_and_state(self) -> None:
        affect = Affect(affection=78, trust=72, loneliness=34, frustration=18)

        block = affect.prompt_block()

        self.assertIn("affection: 78/100", block)
        self.assertIn("loneliness: 34/100", block)
        self.assertIn("Inner state:", block)
        self.assertIn("Behaviors that fit this state:", block)


class AffectPersistenceTests(unittest.TestCase):
    def test_round_trip_through_row(self) -> None:
        affect = Affect(social=42.5, loneliness=63.0, bond=27.0, last_update=1234.0)

        restored = Affect.from_row(affect.to_row())

        self.assertEqual(restored.to_row(), affect.to_row())

    def test_from_row_ignores_unknown_columns(self) -> None:
        restored = Affect.from_row({"social": 30.0, "mystery": 5.0})

        self.assertEqual(restored.snapshot()["social"], 30)


if __name__ == "__main__":
    unittest.main()


class AffectCatchupTests(unittest.TestCase):
    def test_restart_gap_is_bounded(self) -> None:
        from host.affect import MAX_CATCHUP_SECONDS

        # Simulate the host being off for 8 hours: last_update is far in the past.
        overnight = Affect(social=80.0, stimulation=80.0, last_update=1000.0)
        bounded = Affect(social=80.0, stimulation=80.0, last_update=1000.0)

        overnight.advance(now=1000.0 + 8 * 3600.0, ignoring=True)
        bounded.advance(now=1000.0 + MAX_CATCHUP_SECONDS, ignoring=True)

        # The 8h restart applies no more decay than a single clamped catch-up.
        self.assertEqual(overnight.snapshot(), bounded.snapshot())
