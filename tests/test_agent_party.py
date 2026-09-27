"""Confirm Party read (user idea 2026-09-26): real ATK, skill and NP levels."""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "FGO-py"))

from fgo_agent.party import parse_levels, read_confirm_party
from fgo_agent.planner import PlannerPolicy
from fgo_agent.models import Observation


class LevelParsingTests(unittest.TestCase):
    def test_live_ocr_strings(self):
        cases = {"10410/10 1": ([10, 10, 10], 1), "10/5/101": ([10, 5, 10], 1), "10/10/2 +1": ([10, 10, 2], 1),
                 "10/10/101": ([10, 10, 10], 1), "3/3/3+1": ([3, 3, 3], 1), "10710/10 +5": ([10, 10, 10], 5)}
        for text, expected in cases.items():
            self.assertEqual(expected, parse_levels(text), text)


class ConfirmPartyScreenTests(unittest.TestCase):
    def test_the_live_screen_reads_every_member(self):
        old = Path.cwd()
        os.chdir(ROOT / "FGO-py")
        try:
            import cv2
            import fgoDetect
            image = cv2.imread(str(ROOT / "tests/fixtures/story/confirm-party.png"))
            detector = object.__new__(fgoDetect.XDetectNA)
            detector.im = image
            party = read_confirm_party(image, fgoDetect.XDetectNA.ocr.ocrText, detector)
        finally:
            os.chdir(old)
        rows = [(m["level"], m["atk"], m["skill_levels"], m["np_level"]) for m in party["members"]]
        self.assertEqual([(90, 13085, [10, 10, 10], 1), (90, 11616, [10, 5, 10], 1), (90, 13235, [10, 10, 2], 1),
                          (90, 11810, [10, 10, 10], 1), (100, 16172, [3, 3, 3], 1), (120, 13551, [10, 10, 10], 5)],
                         rows)


class PartyInPlannerTests(unittest.TestCase):
    CATALOG = {10: {"atk_max": 10000, "class_attack_rate": 1.0, "class_name": "saber",
                    "cards": ["buster", "buster", "buster", "arts", "quick"],
                    "noble_phantasms": [{"card": "2", "effects": [{"fn": "damageNp", "to": "enemy",
                                                                    "values": [3000, 4000, 4500, 4750, 5000]}]}]},
               20: {"atk_max": 9000, "class_attack_rate": 1.0, "class_name": "caster", "cards": ["arts"] * 5}}
    PARTY = [{"position": 1, "group": "starting", "slot": 1, "atk": 15000, "np_level": 5, "skill_levels": [10, 10, 10]},
             {"position": 4, "group": "sub", "slot": 1, "atk": 12345, "np_level": 1, "skill_levels": [1, 1, 1],
              "class_index": 5}]

    def frame(self, ident, turn):
        return Observation("t", "tactical", "now", wave=1, turn=turn,
                           allies=[{"slot": 1, "id": ident, "hp": 100, "np": 0, "skills": []}],
                           enemies=[{"slot": 1, "hp": 10000}], signals={})

    def test_real_atk_and_np_level_replace_the_catalog_guess(self):
        policy = PlannerPolicy({}, None, strategy_catalog=self.CATALOG, party=self.PARTY)
        policy._track_field(self.frame(10, 1))
        attacker = policy._attackers(self.frame(10, 1))[1]
        self.assertEqual(15000, attacker.atk)
        self.assertEqual(5.0, attacker.np_mult)

    def test_a_replacement_takes_the_matching_sub_member(self):
        policy = PlannerPolicy({}, None, strategy_catalog=self.CATALOG, party=self.PARTY)
        policy._track_field(self.frame(10, 1))
        policy._track_field(self.frame(20, 3))
        self.assertEqual(12345, policy._party_member(1)["atk"])


class StalePartyTests(unittest.TestCase):
    def test_a_party_read_from_another_team_is_ignored(self):
        party = [{"position": 1, "group": "starting", "slot": 1, "atk": 15000, "np_level": 5, "class_index": 5}]
        policy = PlannerPolicy({}, None, strategy_catalog=PartyInPlannerTests.CATALOG, party=party)
        frame = Observation("t", "tactical", "now", wave=1, turn=1,
                            allies=[{"slot": 1, "id": 10, "hp": 100, "np": 0, "skills": []}],  # a saber, not a caster
                            enemies=[{"slot": 1, "hp": 10000}], signals={})
        policy._track_field(frame)
        self.assertIsNone(policy._party_member(1))
        self.assertEqual(10000, policy._attackers(frame)[1].atk)
