"""Regression against a manually inspected NA screenshot, not a live game."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "FGO-py"))


class VisionTests(unittest.TestCase):
    def test_removed_enemy_hud_cannot_authorize_background_ocr_target(self):
        import cv2
        from fgo_agent.observer import LegacyObserver
        from fgo_agent.planner import PlannerPolicy
        with tempfile.TemporaryDirectory() as directory:
            image = cv2.imread(str(ROOT / 'tests/fixtures/na-enemy-removed-phantom-hp.png'))
            state = LegacyObserver(Path(directory), detector_module=self.detector).observe(image)
            self.assertEqual(state.phase, 'tactical')
            self.assertEqual(state.signals['selected_enemy'], 1)
            self.assertEqual(state.enemies[0]['hp'], 724)
            self.assertIsNone(state.enemies[2]['hp'])
            self.assertIn('hp_ocr',state.enemies[2])
            choices = PlannerPolicy({}, None).build_request(state)['allowed_actions']
            self.assertFalse(any(c['kind']=='enemy_target' for c in choices))

    def test_numeric_repair_requires_agreeing_strict_variants(self):
        from fgo_agent.observer import corroborated_integer, strict_integer_consensus
        self.assertEqual(strict_integer_consensus(("12,837", "12837")), 12837)
        self.assertEqual(strict_integer_consensus(("100%", "100"), 500), 100)
        self.assertIsNone(strict_integer_consensus(("12,837", "12,838")))
        self.assertIsNone(strict_integer_consensus(("1.2,837", "12,837")))
        self.assertIsNone(strict_integer_consensus(("", "")))
        self.assertIsNone(strict_integer_consensus(("501%", "501%"), 500))
        self.assertIsNone(strict_integer_consensus(("00%", "00%"), 500))
        self.assertEqual(corroborated_integer("1.2,837", ("12,837", "12837")), 12837)
        self.assertIsNone(corroborated_integer("1.2,837", ("112,837", "112837")))

    def test_padded_numeric_fallback_recovers_real_punctuation_artifacts(self):
        import cv2
        from fgo_agent.observer import LegacyObserver
        expectations = (("na-tactical-padded-hp.png", "hp", 13837),
                        ("na-tactical-padded-np.png", "np", 100))
        with tempfile.TemporaryDirectory() as directory:
            observer = LegacyObserver(Path(directory), detector_module=self.detector)
            for name, field, expected in expectations:
                state = observer.observe(cv2.imread(str(ROOT / "tests/fixtures" / name)))
                self.assertEqual(state.allies[2][field], expected)
                repair = next(r for r in state.signals["numeric_repairs"] if r["field"] == f"ally 3 {field.upper()}")
                self.assertTrue(repair["accepted"])
                self.assertIn("padded_fallback", repair)

    def test_recorded_na_bond_result_is_a_terminal_screen(self):
        import cv2
        from fgo_agent.observer import LegacyObserver
        with tempfile.TemporaryDirectory() as directory:
            frame = cv2.imread(str(ROOT / "tests/fixtures/na-battle-result.png"))
            state = LegacyObserver(Path(directory), detector_module=self.detector).observe(frame)
            self.assertEqual(state.phase, "result")
            self.assertGreaterEqual(state.signals["completion_evidence"]["similarity"], .90)
            self.assertEqual(state.cards, [])

    def test_card_np_portrait_occlusion_carries_same_wave_tactical_turn(self):
        import cv2
        from fgo_agent.observer import LegacyObserver
        with tempfile.TemporaryDirectory() as directory:
            observer = LegacyObserver(Path(directory), detector_module=self.detector)
            tactical = observer.observe(cv2.imread(str(ROOT / "tests/fixtures/na-tactical-wave2.png")))
            cards = observer.observe(cv2.imread(str(ROOT / "tests/fixtures/na-cards-wave2.png")))
            self.assertEqual((tactical.phase, tactical.wave, tactical.turn), ("tactical", 2, 2))
            self.assertEqual((tactical.allies[2]["hp"], tactical.allies[2]["np"]), (12837, 100))
            repairs = {r["field"]: r for r in tactical.signals["numeric_repairs"]}
            self.assertTrue(repairs["ally 3 HP"]["accepted"])
            self.assertTrue(repairs["ally 3 NP"]["accepted"])
            self.assertEqual((cards.phase, cards.wave, cards.turn), ("cards", 2, 2))
            self.assertIn("counter obscured", cards.signals["turn_source"])
            self.assertEqual(len(cards.cards), 8)
            self.assertEqual(cards.allies, tactical.allies)
            with patch.object(self.detector.XDetectNA, "isHouguReady",
                              new=lambda _self, _that=None: [False, False, False]):
                blinking_cards = observer.observe(cv2.imread(str(ROOT / "tests/fixtures/na-cards-wave2.png")))
            self.assertIs(blinking_cards.cards[7]["ready"], True)
            self.assertEqual(blinking_cards.signals["np_readiness"][2]["source"], "same-turn carried ally NP >= 100")
            original_compare = self.detector.XDetectBase._compare

            def sealed_np(detector, template, rect=(0, 0, 1280, 720), threshold=.05):
                if rect == (775, 194, 977, 270):
                    return True
                return original_compare(detector, template, rect, threshold)

            with patch.object(self.detector.XDetectBase, "_compare", new=sealed_np):
                sealed_cards = observer.observe(cv2.imread(str(ROOT / "tests/fixtures/na-cards-wave2.png")))
            self.assertIs(sealed_cards.cards[7]["ready"], False)
            self.assertEqual(sealed_cards.signals["np_readiness"][2]["source"], "NP or Servant seal template")

            def unknown_seal(detector, template, rect=(0, 0, 1280, 720), threshold=.05):
                if rect == (775, 194, 977, 270):
                    raise RuntimeError("seal template unavailable")
                return original_compare(detector, template, rect, threshold)

            with patch.object(self.detector.XDetectBase, "_compare", new=unknown_seal):
                unknown_cards = observer.observe(cv2.imread(str(ROOT / "tests/fixtures/na-cards-wave2.png")))
            self.assertIsNone(unknown_cards.cards[7]["ready"])
            self.assertEqual(unknown_cards.signals["np_readiness"][2]["source"], "seal template unknown")
            observer.last_tactical_context = (1, 1)
            different_wave = observer.observe(cv2.imread(str(ROOT / "tests/fixtures/na-cards-wave2.png")))
            self.assertIsNone(different_wave.turn)

    def test_card_screen_ignores_plausible_occluded_turn_digit_without_same_wave_cache(self):
        import cv2
        from fgo_agent.observer import LegacyObserver
        frame = cv2.imread(str(ROOT / "tests/fixtures/na-cards-obscured-valid-digit.png"))
        with tempfile.TemporaryDirectory() as directory:
            observer = LegacyObserver(Path(directory), detector_module=self.detector)
            observer.last_tactical_context = (3, 3)
            carried = observer.observe(frame)
            self.assertEqual((carried.phase, carried.wave, carried.turn), ("cards", 3, 3))
            self.assertEqual(carried.signals["card_turn_ocr"], {"text": "2", "parsed": 2})
            self.assertIn("last tactical frame", carried.signals["turn_source"])
            observer.last_tactical_context = (2, 3)
            stale = observer.observe(frame)
            self.assertIsNone(stale.turn)
            self.assertEqual(stale.signals["card_turn_ocr"]["parsed"], 2)

    @classmethod
    def setUpClass(cls):
        cls.old_cwd = Path.cwd()
        os.chdir(ROOT / "FGO-py")
        import fgoDetect
        cls.detector = fgoDetect

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls.old_cwd)

    def test_na_tactical_frame_has_consistent_verified_values(self):
        import cv2
        from fgo_agent.observer import LegacyObserver
        frame = cv2.imread(str(ROOT / "tests/fixtures/na-tactical-turn1.png"))
        with tempfile.TemporaryDirectory() as directory:
            observer = LegacyObserver(Path(directory), detector_module=self.detector)
            with patch.object(self.detector.XDetectBase, "screenshot", Mock(side_effect=AssertionError("unexpected extra frame"))):
                state = observer.observe(frame)
            self.assertEqual(state.phase, "tactical")
            self.assertEqual((state.wave, state.turn), (1, 1))
            self.assertEqual([a["hp"] for a in state.allies], [13611, 14081, 14837])
            self.assertEqual([a["np"] for a in state.allies], [13, 0, 75])
            self.assertEqual([a["id"] for a in state.allies], [314, 314, 247])
            self.assertTrue(all(s["ready"] is True for a in state.allies for s in a["skills"]))
            self.assertEqual([e["hp"] for e in state.enemies], [1428, 1428, 1428])
            self.assertEqual([e["charge"] for e in state.enemies], [(0, 4)] * 3)
            self.assertEqual(state.errors, [])

    def test_blank_frame_is_unknown_instead_of_a_ready_battle(self):
        import numpy as np
        from fgo_agent.observer import LegacyObserver
        with tempfile.TemporaryDirectory() as directory:
            state = LegacyObserver(Path(directory), detector_module=self.detector).observe(np.zeros((720, 1280, 3), dtype=np.uint8))
            self.assertEqual(state.phase, "unknown")
            self.assertEqual(state.allies, [])

    def test_recorded_skill_attempt_reprocesses_to_consumed_without_game_input(self):
        import cv2
        from fgo_agent.observer import LegacyObserver
        from fgo_agent.models import Action
        from fgo_agent.controller import BattleController
        with tempfile.TemporaryDirectory() as directory:
            observer = LegacyObserver(Path(directory), detector_module=self.detector, cooldown_ocr=True)
            frames = [observer.observe(cv2.imread(str(ROOT / "tests/fixtures" / name))) for name in
                      ("na-tactical-turn1.png", "na-tactical-after-skill.png", "na-tactical-after-skill-second.png")]
            self.assertEqual(frames[1].allies[2]["np"], 100)
            self.assertEqual(frames[1].allies[2]["skills"][1]["cooldown"], 5)
            self.assertIs(frames[1].allies[2]["skills"][1]["ready"], False)
            driver = Mock()
            driver.observe.side_effect = frames
            result = BattleController(driver, max_polls=2, poll_interval=0).execute(Action("skill", servant=3, skill=2))
            self.assertEqual(result.status, "confirmed")
            self.assertIs(result.skill_consumed, True)
            self.assertIs(result.effect_observed, True)
            driver.send.assert_called_once()

    def test_target_dialog_wins_over_visible_background_attack_button(self):
        import cv2
        from fgo_agent.observer import LegacyObserver
        with tempfile.TemporaryDirectory() as directory:
            frame = cv2.imread(str(ROOT / "tests/fixtures/na-target-selection.png"))
            state = LegacyObserver(Path(directory), detector_module=self.detector, cooldown_ocr=True).observe(frame)
            self.assertEqual(state.phase, "target_selection")
            self.assertEqual(state.target_count, 3)
            self.assertEqual(state.allies, [])  # dimmed background skills are not cooldown evidence
            self.assertEqual(state.errors, [])


if __name__ == "__main__":
    unittest.main()


class FixtureObserverCase(unittest.TestCase):
    """Observes a saved full frame with the real detector."""

    @classmethod
    def setUpClass(cls):
        cls.old_cwd = Path.cwd()
        os.chdir(ROOT / "FGO-py")
        import fgoDetect
        cls.detector = fgoDetect

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls.old_cwd)

    def observe(self, name):
        import cv2
        from fgo_agent.observer import LegacyObserver
        with tempfile.TemporaryDirectory() as directory:
            return LegacyObserver(Path(directory), detector_module=self.detector).observe(
                cv2.imread(str(ROOT / "tests/fixtures" / name)))


class HpBarTests(FixtureObserverCase):
    """Live 2026-09-23: Castoria at 880 HP read as '.880'/'1.880' and was discarded,
    which hid her skills for four turns and stopped the battle after a cast."""

    def test_unreadable_low_hp_still_yields_a_living_servant_with_readable_skills(self):
        state = self.observe("story/castoria-880hp-unreadable.png")
        castoria = next(a for a in state.allies if a["slot"] == 2)
        self.assertIsNone(castoria["hp"], "the misread digits must stay unknown, not be guessed")
        self.assertTrue(castoria["alive"])
        self.assertLess(castoria["hp_fraction"], 0.1)
        self.assertEqual([False, False, False], [s["ready"] for s in castoria["skills"]])
        self.assertGreater(next(a for a in state.allies if a["slot"] == 1)["hp_fraction"], 0.95)

    def test_full_bars_read_as_full_in_every_slot(self):
        state = self.observe("order_change/tactical-before.png")
        for ally in state.allies:
            self.assertGreater(ally["hp_fraction"], 0.95, ally["slot"])


class TurnCounterTests(FixtureObserverCase):
    """Live 2026-09-23: "10 Turn(s)" was cropped to its first digit and read as
    turn 1, so the controller never saw the turn advance and stopped the battle."""

    def test_two_digit_turn_is_read_whole(self):
        self.assertEqual(10, self.observe("story/turn10-two-digit.png").turn)

    def test_a_stray_label_digit_after_one_digit_is_repaired_from_the_previous_turn(self):
        # Live 2026-09-24: "7 Turn(s)" read as 71 right after turn 6.
        import cv2
        from fgo_agent.observer import LegacyObserver
        with tempfile.TemporaryDirectory() as directory:
            observer = LegacyObserver(Path(directory), detector_module=self.detector)
            frames = [cv2.imread(str(ROOT / "tests/fixtures/story" / name))
                      for name in ("turn6.png", "turn7-reads-71.png")]
            self.assertEqual([6, 7], [observer.observe(frame).turn for frame in frames])

    def test_one_digit_turn_does_not_absorb_the_label(self):
        self.assertEqual(9, self.observe("story/turn9-one-digit.png").turn)
        self.assertEqual(1, self.observe("na-tactical-turn1.png").turn)


class OneHpAllyTests(FixtureObserverCase):
    """Live 2026-09-24: a solo story guest at 1 HP had an empty HP bar and unreadable
    digits, so she was treated as dead for six turns with 300% NP and ready skills."""

    def test_readable_np_gauge_keeps_a_one_hp_servant_alive(self):
        for name, np in (("story/one-hp-guest.png", 256), ("story/one-hp-guest-low-np.png", 14)):
            gareth, empty2, empty3 = self.observe(name).allies
            self.assertEqual(np, gareth["np"])
            self.assertTrue(gareth["alive"], name)
            self.assertEqual(256, gareth["id"])
            self.assertTrue(any(s["ready"] for s in gareth["skills"]), name)
            self.assertFalse(empty2["alive"])
            self.assertFalse(empty3["alive"])


class CooldownLabelTests(FixtureObserverCase):
    """Live 2026-09-24: Melusine's ready "Blue Horizon" has dark art (brightness 165),
    so it stayed unknown all battle and its 50% charge was never offered. A cooling
    skill prints "Turn(s)" under its icon; a ready one never does."""

    def test_dark_art_ready_skill_reads_ready(self):
        melusine = self.observe("story/arts-dark-skill-ready.png").allies[2]
        self.assertEqual([True, True, True], [s["ready"] for s in melusine["skills"]])

    def test_turns_label_marks_cooling_skills(self):
        allies = self.observe("story/arts-cooldowns.png").allies
        self.assertEqual([[False] * 3, [False] * 3, [False, False, True]],
                         [[s["ready"] for s in a["skills"]] for a in allies])


class HpLeadingOneTests(FixtureObserverCase):
    """Live 2026-09-25: Castoria's 16,922 HP read as 116,922 on most frames."""

    def test_a_dropped_leading_digit_is_restored_within_the_turn(self):
        # Live 2026-09-25: Oberon's 14,266 read as 4,266 on alternate frames.
        import cv2
        from fgo_agent.observer import LegacyObserver
        with tempfile.TemporaryDirectory() as directory:
            observer = LegacyObserver(Path(directory), detector_module=self.detector)
            readings = [observer.observe(cv2.imread(str(ROOT / "tests/fixtures/story" / name))).allies[0]["hp"]
                        for name in ("hp-14266.png", "hp-14266-reads-4266.png")]
        self.assertEqual([14266, 14266], readings)

    def test_a_stray_leading_one_is_dropped(self):
        allies = self.observe("story/hp-leading-one.png").allies
        self.assertEqual(16922, allies[0]["hp"])


class EnemyGridTests(FixtureObserverCase):
    """Live 2026-09-25: High Queen Morgan's large Berserker icon passed upstream's
    small-icon test, so the 6-enemy grid was used and her 226,408 HP read as
    3,610,802; the false refill forced a crisis re-plan and hid her HP after."""

    def test_a_large_standard_icon_overrules_the_six_enemy_grid(self):
        state = self.observe("story/boss-large-icon-grid.png")
        self.assertEqual("standard3", state.signals["enemy_layout"])
        self.assertEqual([226408], [e["hp"] for e in state.enemies if e.get("hp")])

    def test_a_normal_standard_frame_is_untouched(self):
        self.assertNotIn("enemy_grid_override", self.observe("story/arts-cooldowns.png").signals)


class EnemiesRemainingTests(FixtureObserverCase):
    """The "ENEMY Remaining : N" counter tells a horde respawn from a boss refill."""

    def test_the_remaining_counter_is_read(self):
        self.assertEqual(99, self.observe("story/horde-remaining-99.png").signals["enemies_remaining"])
        self.assertEqual(3, self.observe("story/remaining-3.png").signals["enemies_remaining"])


class BarsRemainingTests(FixtureObserverCase):
    """Live 2026-09-27: Barghest's bar diamonds (lit = bars still to come) were on
    screen, but the agent broke bar 2 believing it was the last."""

    def bars(self, name):
        return [e.get("bars_remaining") for e in self.observe(name).enemies if e.get("hp")]

    def test_lit_diamonds_count_the_bars_left(self):
        self.assertEqual([2], self.bars("story/barghest-bars-2.png"))
        self.assertEqual([1], self.bars("story/barghest-bars-1.png"))
        # Last bar, mid enemy turn (not a tactical frame): two dark diamonds.
        import cv2
        from fgo_agent.observer import _bars_remaining
        self.assertEqual(0, _bars_remaining(cv2.imread(str(ROOT / "tests/fixtures/story/barghest-last-bar.png")), 1))
        self.assertIn(2, self.bars("story/boss-large-icon-grid.png"))

    def test_a_same_coloured_background_is_not_a_diamond(self):
        # Live 2026-09-27 (Cernunnos): pink sky after three diamonds read as five.
        self.assertEqual([3], self.bars("story/bars-pink-background.png"))
        self.assertEqual([2], self.bars("story/np-sealed-two.png"))

    def test_enemies_without_diamonds_have_none_left(self):
        self.assertTrue(all(b == 0 for b in self.bars("story/remaining-3.png")))
        self.assertTrue(all(b == 0 for b in self.bars("story/one-hp-guest.png")))


class StunnedCardTests(FixtureObserverCase):
    """Live 2026-09-24: a stunned Castoria's cards and NP showed NA "Stunned"; upstream's
    CN template missed it, so they were picked and the taps did nothing."""

    def test_stunned_cards_and_np_are_unusable(self):
        state = self.observe("story/cards-stunned.png")
        self.assertEqual("cards", state.phase)
        faces = {c["slot"]: c for c in state.cards if c["slot"] <= 5}
        self.assertEqual([2, 3, 4], [s for s, c in sorted(faces.items()) if c["sealed"]])
        self.assertFalse(faces[1]["sealed"])
        self.assertFalse(faces[5]["sealed"])
        np1 = next(c for c in state.cards if c["slot"] == 6)
        self.assertIsNot(True, np1["ready"])
        from fgo_agent.card_rules import selectable_slots
        self.assertNotIn(6, selectable_slots(state.cards))
        self.assertTrue(set(selectable_slots(state.cards)).isdisjoint({2, 3, 4}))

    def test_two_stunned_servants_leave_a_two_card_chain(self):
        """Live 2026-09-27 (Black Wolf): only Mash's Arts card and NP could be picked;
        demanding three cards stopped the agent with no legal action."""
        from fgo_agent.card_rules import chain_length, selectable_slots
        from fgo_agent.controller import BattleController
        from fgo_agent.models import Action
        state = self.observe("story/two-servants-stunned.png")
        # Live: after Mash's two cards the game waited for a third; stunned cards fill.
        self.assertEqual([1, 6, 2, 3, 4, 5], selectable_slots(state.cards, state.signals, state.turn))
        self.assertEqual(3, chain_length(state.cards, state.signals, state.turn))
        self.assertIsNone(BattleController._validate_cards(None, Action("cards", cards=(6, 1, 2)), state))
        self.assertIsNotNone(BattleController._validate_cards(None, Action("cards", cards=(6, 1)), state))

    def test_np_sealed_label_makes_the_np_unusable(self):
        """Live 2026-09-27 (Cernunnos): the enemy's attack sealed two NPs after their
        Status tabs were read; the cards were tapped and the game waited."""
        state = self.observe("story/np-sealed-two.png")
        nps = {c["slot"]: c for c in state.cards if c["slot"] >= 6}
        self.assertIsNot(True, nps[7]["ready"])
        self.assertIsNot(True, nps[8]["ready"])
        from fgo_agent.card_rules import selectable_slots
        self.assertTrue({7, 8}.isdisjoint(selectable_slots(state.cards, state.signals, state.turn)))
        ready = self.observe("story/two-servants-stunned.png")
        self.assertTrue(next(c for c in ready.cards if c["slot"] == 6)["ready"])

    def test_an_immobilized_np_is_unusable(self):
        """Live 2026-09-27 (Oberon): Tezcatlipoca's NP showed "Immobilized"."""
        state = self.observe("story/np-immobilized.png")
        nps = {c["slot"]: c for c in state.cards if c["slot"] >= 6}
        self.assertIsNot(True, nps[7]["ready"])
        self.assertTrue(nps[6]["ready"])
        self.assertTrue(nps[8]["ready"])

    def test_stunned_cards_fill_only_a_short_hand(self):
        from fgo_agent.card_rules import selectable_slots
        hand = [{"slot": s, "type": "arts", "sealed": True, "stunned": True} for s in (1, 2, 3)]
        hand += [{"slot": 4, "type": "quick", "sealed": False}, {"slot": 5, "type": "buster", "sealed": False}]
        self.assertEqual([4, 5, 1, 2, 3], selectable_slots(hand))
