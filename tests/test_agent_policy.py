import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "FGO-py"))

from fgo_agent import Action, ActionResult, Observation
from fgo_agent.legacy_policy import LegacyPolicy


DATA = {
    10: (0, 0, 0, 0, (0, 0), ((1, 0), (2, 1), (7, 1))),
    11: (0, 0, 0, 0, (1, 0), ((3, 0), (4, 0), (5, 0))),
    12: (0, 0, 0, 0, (2, 0), ((8, 0), (9, 0), (6, 0))),
}


def observation(identifier, phase="tactical", **kwargs):
    return Observation(identifier, phase, "now", **kwargs)


def ally(slot, ident, *, hp=10000, np=0, ready=True, ready_slots=(1,)):
    return {"slot": slot, "id": ident, "hp": hp, "np": np,
            "skills": [{"slot": i, "ready": ready and i in ready_slots} for i in (1, 2, 3)]}


class LegacyPolicyTests(unittest.TestCase):
    def test_effect_classes_choose_their_legacy_gates(self):
        for effect, details in ((1, {}), (2, {"np": 0}), (3, {"np": 100}), (4, {}), (5, {}),
                                (6, {}), (7, {"hp": 6500}), (8, {"enemies": [{"slot": 1, "charge": (1, 1)}]}),
                                (9, {"hp": 3000})):
            with self.subTest(effect=effect):
                data = {99: (0, 0, 0, 0, (0, 0), ((effect, 0), (0, 0), (0, 0)))}
                policy = LegacyPolicy(data)
                action = policy.next_action(observation(str(effect), allies=[ally(1, 99, **{k: v for k, v in details.items() if k != "enemies"})],
                                                        enemies=details.get("enemies", []), wave=1, turn=1))
                self.assertEqual("skill", action.kind)

    def test_effect_class_two_does_not_cast_at_full_np(self):
        policy = LegacyPolicy(DATA)
        self.assertEqual("attack", policy.next_action(observation("full", allies=[ally(1, 10, np=100, ready_slots=(2,))], wave=1, turn=1)).kind)

    def test_metadata_modes_preserve_stage_turn_and_self_threshold_gates(self):
        data = {99: (0, 0, 0, 0, (0, 0), ((2, 3), (2, 5), (7, 4)))}
        policy = LegacyPolicy(data)
        self.assertEqual("attack", policy.next_action(observation("first", allies=[ally(1, 99, np=100)], wave=1, turn=1)).kind)
        self.assertEqual("skill", policy.next_action(observation("later", allies=[ally(1, 99, np=100)], wave=1, turn=1,
                                                        signals={"stage_turn": 2})).kind)
        policy = LegacyPolicy(data)
        self.assertEqual("attack", policy.next_action(observation("self-full", allies=[ally(1, 99, np=100, ready_slots=(2,))], wave=1, turn=1)).kind)

    def test_generic_metadata_target_uses_known_living_default_for_modal_safety(self):
        data = {314: (0, 0, 0, 0, (0, 0), ((2, 1), (2, 1), (4, 1)))}
        policy = LegacyPolicy(data)
        action = policy.next_action(observation("generic", allies=[ally(1, 314, ready_slots=(3,)), ally(2, 10), ally(3, 11)],
                                                wave=1, turn=1))
        self.assertEqual(("skill", 1, 3, 2), (action.kind, action.servant, action.skill, action.target))
        self.assertIn("legacy generic target fallback slot 2", policy.last_decision["reasons"][0])

    def test_effect_one_is_self_targeted_even_when_metadata_mode_is_not_self(self):
        policy = LegacyPolicy({99: (0, 0, 0, 0, (0, 0), ((1, 1), (0, 0), (0, 0)))})
        self.assertEqual(1, policy.next_action(observation("self", allies=[ally(1, 99)], wave=1, turn=1)).target)

    def test_confirmed_skill_is_not_repeated_for_same_wave_turn(self):
        policy = LegacyPolicy(DATA)
        first = policy.next_action(observation("one", allies=[ally(1, 10)], wave=1, turn=1))
        policy.observe_result(first, ActionResult(first, "confirmed", "one", "two", skill_consumed=True))
        self.assertEqual("attack", policy.next_action(observation("two", allies=[ally(1, 10)], wave=1, turn=1)).kind)
        self.assertEqual("skill", policy.next_action(observation("three", allies=[ally(1, 10)], wave=1, turn=2)).kind)

    def test_final_wave_uses_ready_area_np_then_scores_faces(self):
        policy = LegacyPolicy(DATA)
        cards = [
            {"slot": 1, "type": "buster", "servant": 1, "visualgroupid": 1, "affinity": "weak", "critical_rate": 0, "sealed": False},
            {"slot": 2, "type": "arts", "servant": 1, "visualgroupid": 1, "affinity": "neutral", "critical_rate": 0, "sealed": False},
            {"slot": 3, "type": "quick", "servant": 2, "visualgroupid": 2, "affinity": "neutral", "critical_rate": 0, "sealed": False},
            {"slot": 4, "type": "arts", "servant": 2, "visualgroupid": 2, "affinity": "neutral", "critical_rate": 0, "sealed": False},
            {"slot": 5, "type": "buster", "servant": 3, "visualgroupid": 3, "affinity": "resist", "critical_rate": 0, "sealed": False},
            {"slot": 6, "type": "np", "servant": 2, "sealed": False, "ready": True},
        ]
        action = policy.next_action(observation("cards", "cards", allies=[ally(1, 10), ally(2, 11), ally(3, 12)], cards=cards,
                                                enemies=[{"slot": 1, "hp": 1000}], wave=3, signals={"stage_total": 3}))
        self.assertEqual(3, len(action.cards))
        self.assertEqual(6, action.cards[0])
        self.assertEqual(2, len(set(action.cards[1:])))
        self.assertTrue(set(action.cards[1:]).issubset({1, 2, 3, 4, 5}))

    def test_unknown_cards_are_conservative_and_emit_fallback(self):
        policy = LegacyPolicy(DATA)
        action = policy.next_action(observation("unknown", "cards", cards=[
            {"slot": 1, "type": "arts", "sealed": False}, {"slot": 2, "type": "quick", "sealed": False},
            {"slot": 3, "type": "buster", "sealed": False}, {"slot": 4, "type": None, "sealed": None}]))
        self.assertEqual({1, 2, 3}, set(action.cards))
        self.assertIn("card ownership unavailable; visual-group scoring used", policy.last_decision["deviations"])

    def test_support_np_is_unconditional_but_targeted_np_uses_legacy_gate(self):
        policy = LegacyPolicy(DATA)
        cards = [
            {"slot": 1, "type": "arts", "group": 1, "sealed": False},
            {"slot": 2, "type": "arts", "group": 1, "sealed": False},
            {"slot": 3, "type": "arts", "group": 1, "sealed": False},
            {"slot": 6, "type": "np", "servant": 1, "sealed": False, "ready": True},  # targeted kind 0
            {"slot": 8, "type": "np", "servant": 3, "sealed": False, "ready": True},  # support kind 2
        ]
        action = policy.next_action(observation("np-gates", "cards", allies=[ally(1, 10), ally(3, 12)], cards=cards,
                                                enemies=[{"slot": 1, "hp": 1000}], wave=1, signals={"stage_total": 3}))
        self.assertIn(8, action.cards)
        self.assertNotIn(6, action.cards)


if __name__ == "__main__":
    unittest.main()
