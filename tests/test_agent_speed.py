"""Speed work of 2026-09-25: a 9-turn boss fight took 30 minutes, mostly in
screenshots (1 s PNG capture) and observations (1.9 s, re-reading unchanged text)."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "FGO-py"))

from fgo_agent import Action, BattleController, Observation
from fgo_agent.observer import _MemoOcr
from fgo_agent.transport import decode_raw_screencap


def raw(width, height, header=16, fmt=1):
    pixels = np.zeros((height, width, 4), np.uint8)
    pixels[..., 0], pixels[..., 1], pixels[..., 2], pixels[..., 3] = 10, 20, 30, 255  # RGBA
    words = [width, height, fmt] + ([0] if header == 16 else [])
    return b"".join(w.to_bytes(4, "little") for w in words) + pixels.tobytes()


class RawScreencapTests(unittest.TestCase):
    def test_raw_rgba_decodes_to_bgr_with_either_header(self):
        for header in (12, 16):
            image = decode_raw_screencap(raw(32, 18, header))
            self.assertEqual((18, 32, 3), image.shape)
            self.assertEqual([30, 20, 10], image[0, 0].tolist())

    def test_png_or_other_formats_are_not_mistaken_for_raw(self):
        self.assertIsNone(decode_raw_screencap(b"\x89PNG\r\n\x1a\n" + b"\0" * 64))
        self.assertIsNone(decode_raw_screencap(raw(32, 18, fmt=2)))


class MemoOcrTests(unittest.TestCase):
    def test_identical_pixels_are_read_once(self):
        calls = []

        class Engine:
            def ocrText(self, image):
                calls.append(1)
                return str(int(image.sum()))
        memo = _MemoOcr()
        memo.engine = Engine()
        a, b = np.ones((4, 4, 3), np.uint8), np.full((4, 4, 3), 2, np.uint8)
        self.assertEqual(["48", "48", "96"], [memo.ocrText(a), memo.ocrText(a.copy()), memo.ocrText(b)])
        self.assertEqual(2, len(calls))


class FreshFrameTests(unittest.TestCase):
    def frame(self, age):
        stamp = (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat()
        return Observation("f", "tactical", stamp, wave=1, turn=1,
                           allies=[{"slot": 1, "id": 10, "hp": 100, "np": 0, "skills": []}])

    def test_a_moment_old_decision_frame_is_reused(self):
        self.assertTrue(BattleController._fresh(self.frame(0.2)))

    def test_old_or_unstamped_frames_are_captured_again(self):
        self.assertFalse(BattleController._fresh(self.frame(5)))
        self.assertFalse(BattleController._fresh(Observation("f", "tactical", "now")))
        self.assertFalse(BattleController._fresh(None))

    def test_execute_does_not_recapture_a_fresh_frame(self):
        class Driver:
            observed = 0

            def observe(self):
                Driver.observed += 1
                raise RuntimeError("stop after the first capture")

            def send(self, action):
                raise RuntimeError("stop")
        control = BattleController(Driver(), sleeper=lambda _: None, max_polls=1)
        control.execute(Action("attack"), expected=self.frame(0.1))
        before_input = Driver.observed
        Driver.observed = 0
        control.execute(Action("attack"), expected=self.frame(10))
        self.assertEqual(0, before_input, "a fresh frame is not captured again before input")
        self.assertEqual(1, Driver.observed)
