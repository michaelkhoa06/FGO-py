"""Synthetic priority-label composites; no real boss encounter is asserted."""
import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "FGO-py"))

from fgo_agent.enemy_priority import observe_priority_labels
from fgo_agent import Observation
from fgo_agent.planner import PlannerPolicy


class PriorityLabelTests(unittest.TestCase):
    def template(self, name):
        image = cv2.imread(str(ROOT / "FGO-py" / "fgo_agent" / "assets" / "fga-en" / f"target_{name}.png"))
        self.assertIsNotNone(image)
        return image

    def composite(self, *labels):
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        for slot, name in labels:
            template = self.template(name)
            y, x = 50, (slot - 1) * 250 + 80
            image[y:y + template.shape[0], x:x + template.shape[1]] = template
        return image

    @staticmethod
    def enemies(*slots):
        return [{"slot": slot, "hp": 100, "present": True} for slot in slots]

    def test_positive_labels_are_scoped_to_their_fixed_hud_slots(self):
        labels = observe_priority_labels(self.composite((1, "danger"), (3, "servant")), self.enemies(1, 2, 3), grid=0)
        self.assertEqual(["danger", None, "servant"], [enemy["priority_label"] for enemy in labels])
        self.assertGreaterEqual(labels[0]["priority_label_evidence"]["scores"]["danger"], .88)

    def test_negative_and_cross_slot_template_do_not_infer_a_label(self):
        image = self.composite((2, "danger"))
        labels = observe_priority_labels(image, self.enemies(1, 3), grid=0)
        self.assertEqual([None, None], [enemy["priority_label"] for enemy in labels])
        self.assertTrue(all(enemy["priority_label_evidence"]["eligible"] for enemy in labels))

    def test_missing_presence_and_unsupported_grid_are_unknown(self):
        image = self.composite((1, "danger"))
        absent = observe_priority_labels(image, [{"slot": 1, "hp": 100, "present": None}], grid=0)
        unsupported = observe_priority_labels(image, self.enemies(1), grid=2)
        self.assertIsNone(absent[0]["priority_label"])
        self.assertIsNone(unsupported[0]["priority_label"])
        self.assertFalse(absent[0]["priority_label_evidence"]["eligible"])
        self.assertFalse(unsupported[0]["priority_label_evidence"]["eligible"])

    def test_planner_exposes_only_compact_label_without_changing_target_choices(self):
        policy = PlannerPolicy({}, backend=None)
        enemies = observe_priority_labels(self.composite((1, "danger"), (3, "servant")), self.enemies(1, 2, 3), grid=0)
        enemy = enemies[0]
        view = policy._enemy_view(enemy)
        self.assertEqual("danger", view["priority_label"])
        self.assertNotIn("priority_label_evidence", view)
        observation = Observation("priority", "tactical", "now", enemies=enemies, wave=1, turn=1,
                                  signals={"enemy_layout": "standard3", "enemy_target_verifiable": True,
                                           "selected_enemy": 1})
        self.assertEqual(["attack", "e2", "e3"],
                         [action["id"] for action in policy.build_request(observation)["allowed_actions"]])


if __name__ == "__main__":
    unittest.main()
