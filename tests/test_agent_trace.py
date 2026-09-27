import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "FGO-py"))

from fgo_agent import Action, ActionResult, Observation, TraceWriter


class TraceWriterTests(unittest.TestCase):
    def test_jsonl_contains_json_safe_observation_request_and_result(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = TraceWriter(directory, now=lambda: datetime(2026, 9, 21, tzinfo=timezone.utc))
            observation = Observation("o1", "tactical", "now", screen_path=Path("screen.png"))
            action = Action("attack")
            result = ActionResult(action, "confirmed", "o1", "o2", ["cards phase observed"])
            writer.record("observation", observation)
            writer.record("action_request", action)
            writer.record("action_result", result)
            rows = [json.loads(line) for line in Path(directory, "trace.jsonl").read_text().splitlines()]
            self.assertEqual(["observation", "action_request", "action_result"], [row["event"] for row in rows])
            self.assertEqual("screen.png", rows[0]["payload"]["screen_path"])
            self.assertEqual("o2", rows[2]["payload"]["after_observation_id"])
            self.assertTrue(all(row["timestamp"].endswith("+00:00") for row in rows))


if __name__ == "__main__":
    unittest.main()
