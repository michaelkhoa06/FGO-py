"""Recorded NA HUDs plus adversarial mutations of target-highlight evidence."""
import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'FGO-py'))
from fgo_agent.enemy_target import guard_enemy_presence, target_signals


class EnemyHighlightTests(unittest.TestCase):
    def detect(self, image, **kwargs):
        return target_signals(image, kwargs.pop('enemies', [{'slot':i, 'hp':9843} for i in (1,2,3)]),
                              phase=kwargs.pop('phase', 'tactical'), grid=kwargs.pop('grid', 0))

    def frame(self, slot):
        im = cv2.imread(str(ROOT / f'tests/fixtures/na-enemy-selected-{slot}.png'))
        self.assertIsNotNone(im)
        return im

    def test_recorded_class_independent_highlights_in_all_three_slots(self):
        for slot in (1, 2, 3):
            with self.subTest(slot=slot):
                result = self.detect(self.frame(slot))
                self.assertEqual(result['selected_enemy'], slot)
                self.assertIs(result['enemy_target_verifiable'], True)

    def test_missing_duplicate_or_filled_blue_highlights_are_unknown(self):
        image = self.frame(1)
        missing = image.copy()
        missing[:90,:750] = 0
        duplicate = image.copy()
        duplicate[:80,250:335] = image[:80,:85]
        filled = image.copy()
        filled[:90,:750] = (255,140,0)
        for mutation in (missing, duplicate, filled):
            self.assertIsNone(self.detect(mutation)['selected_enemy'])
            self.assertIs(self.detect(mutation)['enemy_target_verifiable'], False)

    def test_modal_cards_six_grid_unknown_hp_and_wrong_size_cannot_authorize(self):
        image = self.frame(1)
        for phase in ('cards', 'target_selection', 'result', 'unknown'):
            self.assertFalse(self.detect(image, phase=phase)['enemy_target_verifiable'])
        for grid in (2, None, 1):
            self.assertFalse(self.detect(image, grid=grid)['enemy_target_verifiable'])
        for hp in (None, 0, -1, True):
            self.assertFalse(self.detect(image, enemies=[{'slot':1,'hp':hp}])['enemy_target_verifiable'])
        self.assertFalse(self.detect(image[:360,:640])['enemy_target_verifiable'])

    def test_unrelated_scene_pixels_do_not_change_hud_selection(self):
        image = self.frame(2)
        image[100:] = np.random.default_rng(1).integers(0,256,image[100:].shape,dtype=np.uint8)
        self.assertEqual(self.detect(image)['selected_enemy'], 2)

    def test_removed_enemy_background_digits_never_become_actionable_hp(self):
        image = cv2.imread(str(ROOT / 'tests/fixtures/na-enemy-removed-phantom-hp.png'))
        enemies = guard_enemy_presence(image, [{'slot':1,'hp':724},{'slot':2,'hp':None},
                                                {'slot':3,'hp':3}], grid=0)
        self.assertEqual(enemies[0]['hp'],724)
        self.assertTrue(enemies[0]['present'])
        self.assertIsNone(enemies[2]['hp'])
        self.assertIsNone(enemies[2]['present'])
        self.assertEqual(enemies[2]['hp_ocr'],3)
        self.assertEqual(self.detect(image,enemies=enemies)['selected_enemy'],1)

    def test_present_hud_preserves_hp_without_claiming_full_white_panel_is_enemy(self):
        for slot in (1,2,3):
            image = self.frame(slot)
            enemies = guard_enemy_presence(image,[{'slot':i,'hp':9843} for i in (1,2,3)],grid=0)
            self.assertTrue(all(e['present'] is True and e['hp']==9843 for e in enemies))
        image[:90,:750] = 255
        enemies = guard_enemy_presence(image,[{'slot':1,'hp':9843}],grid=0)
        self.assertIsNone(enemies[0]['hp'])

    def test_original_bronze_rank_huds_preserve_verified_hp(self):
        for name in ('na-tactical-turn1.png','na-tactical-wave2.png'):
            with self.subTest(name=name):
                image=cv2.imread(str(ROOT / 'tests/fixtures' / name))
                enemies=guard_enemy_presence(image,[{'slot':i,'hp':1428} for i in (1,2,3)],grid=0)
                self.assertTrue(all(e['present'] is True and e['hp']==1428 for e in enemies))


if __name__ == '__main__':
    unittest.main()
