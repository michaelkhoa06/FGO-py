"""Formula-based damage estimates (user request 2026-09-26): compare an NP with a
buffed servant's face cards using Atlas numbers."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "FGO-py"))

from fgo_agent.damage import Attacker, Buffs, BuffTracker, chain_damage
from fgo_agent.models import Action, ActionResult, Observation
from fgo_agent.planner import PlannerPolicy

HERO = Attacker(slot=1, atk=10000, class_rate=1.0, class_name="saber", np_card="buster", np_mult=9.0)


class FormulaTests(unittest.TestCase):
    def test_a_buster_np_brave_chain_matches_the_hand_calculation(self):
        # NP 10000*0.23*9*1.5 = 31050; B2 (0.5+1.8)*2300+2000 = 7290; B3 (0.5+2.1)*2300+2000 = 7980;
        # extra (0.5+1)*2300*3.5 = 12075 (same-colour brave chain).
        result = chain_damage([(1, "buster", True), (1, "buster", False), (1, "buster", False)], {1: HERO}, {})
        self.assertEqual([31050, 7290, 7980], result["cards"])
        self.assertEqual(12075, result["extra"])
        self.assertEqual(58395, result["total"])

    def test_card_buffs_and_class_affinity_apply(self):
        buffed = Buffs(card={"arts": 0.5, "buster": 0.0, "quick": 0.0}, atk=0.2)
        plain = chain_damage([(1, "arts", False), (1, "arts", False), (1, "arts", False)], {1: HERO}, {})
        strong = chain_damage([(1, "arts", False), (1, "arts", False), (1, "arts", False)], {1: HERO}, {1: buffed},
                              defender_class="lancer", relation={"saber": {"lancer": 2000}})
        self.assertGreater(strong["total"], plain["total"] * 2)


class TrackerTests(unittest.TestCase):
    SWORD = [{"fn": "addStateShort", "to": "ptOne", "buff": "upCommandall", "values": [300] * 9 + [500],
              "turns": 3, "card": ["arts"]},
             {"fn": "addStateShort", "to": "ptOne", "buff": "upDamage", "values": [300] * 9 + [500], "turns": 3,
              "target_traits": ["threatToHumanity"]}]

    def test_a_targeted_card_buff_lasts_its_turns_and_trait_bonuses_stay_conditional(self):
        tracker = BuffTracker()
        tracker.apply(self.SWORD, caster=1, target=2, turn=1, level_index=10, field_slots=[1, 2, 3])
        self.assertAlmostEqual(0.5, tracker.buffs_for(2).card["arts"])
        self.assertEqual(0.0, tracker.buffs_for(2).power, "a trait-dependent bonus is not assumed")
        self.assertEqual(1, len(tracker.buffs_for(2).conditional))
        tracker.expire(4)
        self.assertEqual(0.0, tracker.buffs_for(2).card["arts"])

    def test_an_effect_that_requires_a_state_waits_for_it(self):
        grant = [{"fn": "addState", "to": "self", "buff": "addIndividuality", "values": [2857], "turns": 3,
                  "grants_trait": 2857}]
        needs = [{"fn": "addStateShort", "to": "self", "buff": "upAtk", "values": [200], "turns": 3,
                  "requires": [2857]}]
        tracker = BuffTracker()
        tracker.apply(needs, caster=1, target=None, turn=1, level_index=1, field_slots=[1])
        self.assertEqual(0.0, tracker.buffs_for(1).atk)
        tracker.apply(grant, caster=1, target=None, turn=1, level_index=1, field_slots=[1])
        tracker.apply(needs, caster=1, target=None, turn=1, level_index=1, field_slots=[1])
        self.assertAlmostEqual(0.2, tracker.buffs_for(1).atk)


class PlannerEstimateTests(unittest.TestCase):
    CATALOG = {10: {"atk_max": 10000, "class_attack_rate": 1.0, "class_name": "saber",
                    "cards": ["quick", "arts", "arts", "buster", "buster"],
                    "skills": [{"slot": 1, "variants": [{"effects": TrackerTests.SWORD}]}],
                    "noble_phantasms": [{"card": "2", "effects": [{"fn": "damageNp", "to": "enemy",
                                                                    "values": [9000, 12000]}]}]}}

    def frame(self, phase="tactical"):
        return Observation("t", phase, "now", wave=1, turn=1,
                           allies=[{"slot": 1, "id": 10, "hp": 1000, "np": 100,
                                    "skills": [{"slot": 1, "ready": True}]}],
                           enemies=[{"slot": 1, "hp": 500000, "class_name": "lancer"}], signals={"stage_total": 1})

    def test_the_request_carries_per_ally_estimates_and_confirmed_buffs_raise_them(self):
        policy = PlannerPolicy({}, None, strategy_catalog=self.CATALOG)
        before = policy.build_request(self.frame())["damage_estimates"]["allies"][0]
        self.assertTrue(before["np_ready"])
        self.assertIn("np_alone", before)
        policy._track_field(self.frame())
        policy._record_buffs(Action("skill", 1, 1, 1), ActionResult(Action("skill", 1, 1, 1), "confirmed", "a", "b",
                                                                      skill_consumed=True))
        after = policy.build_request(self.frame())["damage_estimates"]["allies"][0]
        self.assertGreater(after["three_own_cards_with_extra"], before["three_own_cards_with_extra"])
        self.assertEqual(50, after["tracked_buffs"][0]["value_pct"])


class DuplicateServantTests(unittest.TestCase):
    """Live 2026-09-26 (double Koyanskaya): face cards of two copies had no owner,
    so no chain could be estimated and turn 2 fell back to the old scorer."""

    def test_an_ambiguous_face_card_uses_the_lower_estimate_over_its_copies(self):
        catalog = {10: PlannerEstimateTests.CATALOG[10]}
        allies = [{"slot": 1, "id": 10, "hp": 1, "np": 0, "skills": []},
                  {"slot": 2, "id": 10, "hp": 1, "np": 0, "skills": []}]
        cards = [{"slot": i, "type": "buster", "servant": None, "servant_id": 10, "sealed": False} for i in (1, 2, 3)]
        frame = Observation("c", "cards", "now", allies=allies, cards=cards, wave=1, turn=1,
                            enemies=[{"slot": 1, "hp": 90000, "class_name": "saber"}], signals={})
        policy = PlannerPolicy({}, None, strategy_catalog=catalog)
        self.assertIsNotNone(policy._chain_estimate(frame, (1, 2, 3)))


class CalibrationTests(unittest.TestCase):
    """Live 2026-09-26 (Avenger): estimates were 1.94x/1.93x below the real HP drop."""

    def test_a_clean_single_target_turn_calibrates_later_estimates(self):
        catalog = {10: PlannerEstimateTests.CATALOG[10]}
        allies = [{"slot": 1, "id": 10, "hp": 1000, "np": 100, "skills": []}]
        faces = [{"slot": i, "type": "buster", "servant": 1, "sealed": False} for i in (1, 2, 3)]
        before = Observation("c", "cards", "now", allies=allies, cards=faces, wave=1, turn=1,
                             enemies=[{"slot": 1, "hp": 500000, "class_name": "saber"}], signals={})
        policy = PlannerPolicy({}, None, strategy_catalog=catalog)
        policy._track_field(before)
        estimate = policy._chain_estimate(before, (1, 2, 3))
        action = Action("cards", cards=(1, 2, 3))
        policy._record_buffs(action, ActionResult(action, "confirmed", "a", "b"))
        after = Observation("t", "tactical", "now", allies=allies, wave=1, turn=2,
                            enemies=[{"slot": 1, "hp": 500000 - 2 * estimate, "class_name": "saber"}], signals={})
        policy._track_field(after)
        self.assertAlmostEqual(2.0, policy._calibration_factor(), places=2)
        view = policy._damage_view(after)
        self.assertEqual(2, round(view["calibration"]["factor"]))
