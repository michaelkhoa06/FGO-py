import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "FGO-py"))
from fgoAgent import load_plan
from fgo_agent.observer import strict_integer


class CliTests(unittest.TestCase):
    def test_whole_plan_is_validated_before_device_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "plan.json"
            for bad in ([{"kind": "skill", "servant": True, "skill": 1}],
                        [{"kind": "skill_target", "target": 1}],
                        [{"kind": "attack"}, {"kind": "cards", "cards": [1, 1, 2]}]):
                p.write_text(json.dumps(bad))
                with self.assertRaises(ValueError):
                    load_plan(p)
            p.write_text('[{"kind":"skill","servant":3,"skill":2}]')
            self.assertEqual(load_plan(p)[0].servant, 3)

    def test_unreadable_numbers_are_not_zero(self):
        for bad in ("", "13!i", "1.3%", "75O%", "-1"):
            self.assertIsNone(strict_integer(bad))
        self.assertEqual(strict_integer("0%"), 0)
        self.assertEqual(strict_integer("14,837"), 14837)

    def test_enemy_target_plan_requires_explicit_integer_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'plan.json'
            for target in (None, True, 0, 4):
                p.write_text(json.dumps([{'kind':'enemy_target','target':target}]))
                with self.assertRaises(ValueError):
                    load_plan(p)
            p.write_text('[{"kind":"enemy_target","target":2}]')
            self.assertEqual(load_plan(p)[0].target, 2)


if __name__ == "__main__":
    unittest.main()
