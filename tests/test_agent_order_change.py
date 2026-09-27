"""Order Change recognition on real NA frames captured live on 2026-09-22.

The fixtures come from one throwaway Novice battle in which the operator opened
the master-skill palette, opened and cancelled Order Change, then swapped the
support Koyanskaya for Oberon. They are calibration frames, not policy evidence.
"""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "FGO-py"))
FIXTURES = ROOT / "tests/fixtures/order_change"


def frame(name):
    import cv2
    image = cv2.imread(str(FIXTURES / name))
    if image is None:
        raise FileNotFoundError(name)
    return image


class OrderChangeRecognitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_cwd = Path.cwd()
        os.chdir(ROOT / "FGO-py")
        import fgoDetect
        detector = object.__new__(fgoDetect.XDetectNA)
        cls.ocr = staticmethod(lambda crop: detector.ocr.ocrText(crop))

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls.old_cwd)

    def test_member_screen_reads_names_and_no_selection(self):
        from fgo_agent.order_change import read_order_change
        state = read_order_change(frame("order-change-none-selected.png"), self.ocr)
        self.assertIsNotNone(state)
        self.assertEqual(["Koyanskaya of Light", "Koyanskaya of Light", "Arjuna (Alter)",
                          "Oberon", "Nitocris (Alter)", "Space Ereshkigal"],
                         [m["name"] for m in state["members"]])
        self.assertEqual(["starting"] * 3 + ["sub"] * 3, [m["group"] for m in state["members"]])
        self.assertEqual([1, 2, 3, 1, 2, 3], [m["slot"] for m in state["members"]])
        self.assertFalse(any(m["selected"] for m in state["members"]))
        self.assertFalse(state["replace_enabled"])

    def test_selection_markers_and_replace_state_follow_the_frame(self):
        from fgo_agent.order_change import read_order_change
        one = read_order_change(frame("order-change-one-selected.png"), self.ocr)
        self.assertEqual([True, False, False, False, False, False], [m["selected"] for m in one["members"]])
        self.assertFalse(one["replace_enabled"])
        two = read_order_change(frame("order-change-two-selected.png"), self.ocr)
        self.assertEqual([True, False, False, True, False, False], [m["selected"] for m in two["members"]])
        self.assertTrue(two["replace_enabled"])

    def test_member_screen_is_not_recognized_on_other_frames(self):
        from fgo_agent.order_change import read_order_change
        for name in ("tactical-before.png", "palette-open.png", "tactical-after-swap.png",
                     "palette-order-change-used.png"):
            with self.subTest(name):
                self.assertIsNone(read_order_change(frame(name), self.ocr))

    def test_palette_open_and_order_change_state(self):
        from fgo_agent.order_change import read_palette
        self.assertEqual("ready", read_palette(frame("palette-open.png"))["order_change"])
        self.assertEqual("used", read_palette(frame("palette-order-change-used.png"))["order_change"])
        for name in ("tactical-before.png", "tactical-after-swap.png", "order-change-none-selected.png"):
            with self.subTest(name):
                self.assertIsNone(read_palette(frame(name)))

    def test_observer_recognizes_the_member_screen_and_the_swapped_servant(self):
        import tempfile
        import fgoDetect
        from fgo_agent.observer import LegacyObserver
        with tempfile.TemporaryDirectory() as directory:
            observer = LegacyObserver(Path(directory), detector_module=fgoDetect)
            screen = observer.observe(frame("order-change-none-selected.png"))
            self.assertEqual("order_change", screen.phase)
            self.assertEqual("Oberon", screen.signals["order_change"]["members"][3]["name"])
            self.assertNotIn("Unrecognized modal dialog; background battle controls are not actionable.",
                             screen.errors)
            after = observer.observe(frame("tactical-after-swap.png"))
            self.assertEqual("tactical", after.phase)
            self.assertEqual(316, next(a["id"] for a in after.allies if a["slot"] == 1))
            palette = observer.observe(frame("palette-open.png"))
            self.assertEqual("ready", palette.signals["master_palette"]["order_change"])


class NameTests(unittest.TestCase):
    def test_normalization_repairs_only_observed_ocr_noise(self):
        from fgo_agent.order_change import normalize_name
        self.assertEqual("Koyanskaya of Light", normalize_name("Koyanskaya of Light y"))
        self.assertEqual("Koyanskaya of Light", normalize_name("Koyanskaya of Light "))
        self.assertEqual("Nitocris (Alter)", normalize_name("Nitocris (Alter"))
        self.assertEqual("Mysterious Heroine X", normalize_name("Mysterious Heroine X"))
        self.assertEqual("Oberon", normalize_name("Oberon+++"))
        self.assertEqual("Space Ereshkigal", normalize_name("/Space Ereshkigal"))
        self.assertIsNone(normalize_name(""))
        self.assertIsNone(normalize_name("+++"))

    def test_names_match_tolerates_live_ocr_noise_but_not_other_servants(self):
        from fgo_agent.order_change import names_match
        self.assertTrue(names_match("Oberon+++", "Oberon"))
        self.assertTrue(names_match("Nitocris (Alter", "Nitocris (Alter)"))
        self.assertFalse(names_match("Nitocris (Alter)", "Oberon"))
        self.assertFalse(names_match("Koyanskaya of Light", "Koyanskaya of Dark"))
        self.assertFalse(names_match(None, "Oberon"))

    def test_resolution_refuses_ambiguous_names(self):
        from fgo_agent.order_change import resolve_servant_id
        index = {284: {"name": "Altria Caster"}, 386: {"name": "Altria Caster"},
                 316: {"name": "Oberon"}, 196: {"name": "Ereshkigal"}, 417: {"name": "Ereshkigal"}}
        self.assertEqual(316, resolve_servant_id("Oberon", index))
        self.assertEqual(316, resolve_servant_id("oberon", index))
        self.assertIsNone(resolve_servant_id("Altria Caster", index))
        self.assertIsNone(resolve_servant_id("Space Ereshkigal", index))
        self.assertIsNone(resolve_servant_id(None, index))
