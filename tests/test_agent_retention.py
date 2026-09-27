"""Only the newest run folders are kept; nothing else in agent-runs is touched."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "FGO-py"))

from fgo_agent.retention import prune_runs


class RetentionTests(unittest.TestCase):
    def make(self, root, names):
        for name in names:
            (root / name).mkdir()
            (root / name / "trace.jsonl").write_text("{}", encoding="utf-8")

    def test_only_the_newest_sessions_of_each_kind_survive(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sessions = [f"2026092{d}T000000Z-0000000{d}" for d in range(1, 9)]
            replays = [f"replay-2026092{d}T000000Z-0000000{d}" for d in range(1, 8)]
            self.make(root, sessions + replays + ["validation-setup", "order-change-capture"])
            (root / "validation_setup.py").write_text("tool", encoding="utf-8")
            deleted = prune_runs(root, keep=5)
            self.assertEqual(sorted(sessions[:3] + replays[:2]), sorted(p.name for p in deleted))
            left = sorted(p.name for p in root.iterdir())
            for name in sessions[3:] + replays[2:] + ["validation-setup", "order-change-capture", "validation_setup.py"]:
                self.assertIn(name, left)

    def test_the_current_and_kept_folders_are_never_deleted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            names = [f"2026092{d}T000000Z-0000000{d}" for d in range(1, 5)]
            self.make(root, names)
            (root / names[0] / "KEEP").write_text("", encoding="utf-8")
            deleted = prune_runs(root, keep=1, current=root / names[1])
            self.assertEqual([names[2]], [p.name for p in deleted])

    def test_bad_arguments_and_missing_roots(self):
        with self.assertRaises(ValueError):
            prune_runs(Path("."), keep=0)
        self.assertEqual([], prune_runs(Path("does-not-exist-anywhere"), keep=5))


if __name__ == "__main__":
    unittest.main()
