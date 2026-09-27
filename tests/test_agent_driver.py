import sys
from pathlib import Path
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "FGO-py"))
from fgo_agent.models import Action, Observation
from fgo_agent.driver import BattleDriver


class DriverTests(unittest.TestCase):
    def test_three_target_dialog_has_unambiguous_slots_even_with_unreadable_hp(self):
        device = Mock()
        driver = BattleDriver(device, Mock())
        driver.active_slots = [1, 2]
        driver.latest = Observation("a", "target_selection", "now", target_count=3)
        driver.send(Action("skill_target", target=2))
        device.tap.assert_called_once_with(649, 440)

    def test_stage_turn_tracks_new_waves_but_midwave_start_is_unknown(self):
        observer = Mock()
        observer.observe.side_effect = [Observation("a", "tactical", "now", wave=1, turn=4),
                                       Observation("b", "tactical", "now", wave=2, turn=5),
                                       Observation("c", "tactical", "now", wave=2, turn=6)]
        driver = BattleDriver(Mock(), observer)
        self.assertIsNone(driver.observe().signals["stage_turn"])
        self.assertEqual(driver.observe().signals["stage_turn"], 1)
        self.assertEqual(driver.observe().signals["stage_turn"], 2)

    def test_two_remaining_allies_use_dialog_positions_not_field_coordinates(self):
        device = Mock()
        driver = BattleDriver(device, Mock())
        driver.active_slots = [1, 3]
        driver.latest = Observation("a", "target_selection", "now", target_count=2)
        driver.send(Action("skill_target", target=3))
        device.tap.assert_called_once_with(875, 440)

    def test_uncertain_target_count_never_taps(self):
        device = Mock()
        driver = BattleDriver(device, Mock())
        driver.active_slots = [1, 2, 3]
        driver.latest = Observation("a", "target_selection", "now", target_count=2)
        with self.assertRaises(ValueError):
            driver.send(Action("skill_target", target=3))
        device.tap.assert_not_called()

    def test_can_target_reuses_target_dialog_mapping_guard(self):
        driver = BattleDriver(Mock(), Mock())
        driver.latest = Observation("a", "target_selection", "now", target_count=2)
        driver.active_slots = [1, 3]
        self.assertTrue(driver.can_target(3))
        self.assertFalse(driver.can_target(2))
        driver.latest.target_count = 3
        self.assertTrue(driver.can_target(2))

    def test_observation_tracks_numeric_turn_change_only(self):
        observer = Mock()
        observer.observe.side_effect = [Observation("a", "tactical", "now", wave=1, turn=1),
                                       Observation("b", "unknown", "now"),
                                       Observation("c", "tactical", "now", wave=1, turn=1),
                                       Observation("d", "tactical", "now", wave=1, turn=2),
                                       Observation("e", "tactical", "now", wave=2, turn=1)]
        driver = BattleDriver(Mock(), observer)
        self.assertFalse(driver.observe().signals["turn_advanced"])
        driver.observe()
        self.assertFalse(driver.observe().signals["turn_advanced"])
        self.assertTrue(driver.observe().signals["turn_advanced"])
        self.assertTrue(driver.observe().signals["turn_advanced"])

    def test_enemy_target_is_deferred_without_sending_input(self):
        device = Mock()
        driver = BattleDriver(device, Mock())
        driver.latest = Observation("a", "tactical", "now", enemies=[dict(slot=1, hp=100)])
        with self.assertRaises(ValueError):
            driver.send(Action("enemy_target", target=1))
        device.tap.assert_not_called()

    def test_enemy_target_uses_standard_three_enemy_coordinates_only_when_verified(self):
        device = Mock()
        driver = BattleDriver(device, Mock())
        driver.latest = Observation("a", "tactical", "now", wave=1, turn=1, enemies=[{"slot": 1, "hp": 100}, {"slot": 2, "hp": 100}],
                                    signals={"enemy_layout": "standard3", "enemy_target_verifiable": True,
                                             "selected_enemy": 1})
        driver.send(Action("enemy_target", target=2))
        device.tap.assert_called_once_with(342, 40)

    def test_enemy_target_driver_rejects_modal_unknown_or_boolean_context(self):
        device = Mock()
        driver = BattleDriver(device, Mock())
        driver.latest = Observation("a", "target_selection", "now", wave=1, turn=1, enemies=[{"slot": 2, "hp": 100}],
                                    signals={"enemy_layout": "standard3", "enemy_target_verifiable": True,
                                             "selected_enemy": 1})
        with self.assertRaises(ValueError):
            driver.send(Action("enemy_target", target=2))
        driver.latest.phase = "tactical"
        driver.latest.wave = True
        with self.assertRaises(ValueError):
            driver.send(Action("enemy_target", target=2))
        device.tap.assert_not_called()

    def test_enemy_target_driver_requires_positive_selected_hp_and_unambiguous_slots(self):
        device = Mock()
        driver = BattleDriver(device, Mock())
        signals = {"enemy_layout": "standard3", "enemy_target_verifiable": True, "selected_enemy": 1}
        for wave, enemies in [
            (0, [{"slot": 1, "hp": 100}, {"slot": 2, "hp": 100}]),
            (1, [{"slot": 1, "hp": 0}, {"slot": 2, "hp": 100}]),
            (1, [{"slot": 1, "hp": 100}, {"slot": 2, "hp": 100}, {"slot": 2, "hp": 100}]),
            (1, [{"slot": True, "hp": 100}, {"slot": 2, "hp": 100}]),
        ]:
            driver.latest = Observation("a", "tactical", "now", wave=wave, turn=1, enemies=enemies, signals=signals)
            with self.assertRaises(ValueError):
                driver.send(Action("enemy_target", target=2))
        device.tap.assert_not_called()


if __name__ == "__main__":
    unittest.main()
