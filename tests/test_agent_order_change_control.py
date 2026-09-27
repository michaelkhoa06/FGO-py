"""The Order Change sequence: every step verified, and nothing spent on a back-out."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "FGO-py"))

from fgo_agent import Action, BattleController, Observation
from fgo_agent.driver import BattleDriver


NAMES = ["Koyanskaya of Light", "Koyanskaya of Light", "Arjuna (Alter)",
         "Oberon", "Nitocris (Alter)", "Space Ereshkigal"]
BACKLINE = [{"slot": 1, "name": "Oberon", "servant_id": 316},
            {"slot": 2, "name": "Nitocris (Alter)", "servant_id": 370},
            {"slot": 3, "name": "Space Ereshkigal", "servant_id": None}]


def allies(first=314):
    return [{"slot": 1, "id": first, "hp": 15000, "np": 0, "skills": []},
            {"slot": 2, "id": 314, "hp": 14000, "np": 0, "skills": []},
            {"slot": 3, "id": 247, "hp": 14800, "np": 75, "skills": []}]


def tactical(name, *, palette=None, first=314, available=True):
    signals = {"backline": [dict(m) for m in BACKLINE], "order_change_available": available}
    if palette:
        signals["master_palette"] = {"mystic_code": "chaldea_combat_uniform", "order_change": palette}
    return Observation(name, "tactical", "now", allies=allies(first), wave=3, turn=3, signals=signals)


def member_screen(name, selected=(), names=NAMES, replace=False):
    members = [{"position": i + 1, "group": "starting" if i < 3 else "sub",
                "slot": i + 1 if i < 3 else i - 2, "name": n, "selected": (i + 1) in selected}
               for i, n in enumerate(names)]
    return Observation(name, "order_change", "now",
                       signals={"order_change": {"members": members, "replace_enabled": replace}})


class ScriptedDriver:
    def __init__(self, observations):
        self.observations, self.sent, self.marked = iter(observations), [], []

    def observe(self):
        return next(self.observations)

    def send(self, action):
        self.sent.append((action.kind, action.skill, action.target))

    def mark_order_change_used(self, field, sub, outgoing):
        self.marked.append((field, sub, outgoing))


def controller(frames):
    driver = ScriptedDriver(frames)
    return BattleController(driver, sleeper=lambda _: None, max_polls=4), driver


SWAP = Action("order_change", servant=1, target=1)


class OrderChangeSequenceTests(unittest.TestCase):
    def test_verified_swap_is_confirmed_by_the_field_servant_changing(self):
        control, driver = controller([
            tactical("before"), tactical("palette", palette="ready"), member_screen("screen"),
            member_screen("one", selected={1}), member_screen("two", selected={1, 4}, replace=True),
            tactical("after-1", first=316), tactical("after-2", first=316)])
        result = control.execute(SWAP)
        self.assertEqual("confirmed", result.status, result.reason)
        self.assertEqual([("master_menu", None, None), ("master_skill", 3, None),
                          ("order_change_member", None, 1), ("order_change_member", None, 4),
                          ("order_change_confirm", None, None)], driver.sent)
        self.assertEqual([(1, 1, "Koyanskaya of Light")], driver.marked)

    def test_wrong_back_line_member_backs_out_unspent(self):
        shuffled = NAMES[:3] + ["Nitocris (Alter)", "Oberon", "Space Ereshkigal"]
        control, driver = controller([
            tactical("before"), tactical("palette", palette="ready"), member_screen("screen", names=shuffled),
            tactical("back", palette="ready"), tactical("closed")])
        result = control.execute(SWAP)
        self.assertEqual("rejected", result.status)
        self.assertIn("cancelled unspent", result.reason)
        kinds = [kind for kind, _, _ in driver.sent]
        self.assertEqual(["master_menu", "master_skill", "order_change_cancel", "master_menu"], kinds)
        self.assertNotIn("order_change_confirm", kinds)
        self.assertEqual([], driver.marked)

    def test_spent_order_change_is_refused_before_the_member_screen(self):
        control, driver = controller([tactical("before"), tactical("palette", palette="used"), tactical("closed")])
        result = control.execute(SWAP)
        self.assertEqual("rejected", result.status)
        self.assertEqual(["master_menu", "master_menu"], [kind for kind, _, _ in driver.sent])

    def test_unobserved_selection_backs_out_without_replace(self):
        control, driver = controller([
            tactical("before"), tactical("palette", palette="ready"), member_screen("screen"),
            *[member_screen(f"nothing-{i}") for i in range(4)],
            tactical("back", palette="ready"), tactical("closed")])
        result = control.execute(SWAP)
        self.assertEqual("rejected", result.status)
        self.assertNotIn("order_change_confirm", [kind for kind, _, _ in driver.sent])

    def test_unconfirmed_swap_after_replace_is_uncertain(self):
        control, _ = controller([
            tactical("before"), tactical("palette", palette="ready"), member_screen("screen"),
            member_screen("one", selected={1}), member_screen("two", selected={1, 4}, replace=True),
            *[tactical(f"unchanged-{i}") for i in range(4)]])
        result = control.execute(SWAP)
        self.assertEqual("uncertain", result.status)
        self.assertIn("not confirmed", result.reason)

    def test_unavailable_order_change_sends_nothing(self):
        control, driver = controller([tactical("before", available=False)])
        result = control.execute(SWAP)
        self.assertEqual("rejected", result.status)
        self.assertEqual([], driver.sent)

    def test_reconnaissance_reads_the_back_line_and_leaves_the_skill_unspent(self):
        control, driver = controller([
            tactical("start"), tactical("palette", palette="ready"), member_screen("screen"),
            tactical("back", palette="ready"), tactical("closed")])
        report = control.reconnoitre_order_change()
        self.assertEqual("ok", report["status"], report)
        self.assertEqual(["Oberon", "Nitocris (Alter)", "Space Ereshkigal"], [m["name"] for m in report["backline"]])
        kinds = [kind for kind, _, _ in driver.sent]
        self.assertEqual(["master_menu", "master_skill", "order_change_cancel", "master_menu"], kinds)
        self.assertNotIn("order_change_confirm", kinds)


class DriverOrderChangeTrackingTests(unittest.TestCase):
    @staticmethod
    def driver(frames, **kwargs):
        observer = Mock()
        observer.observe.side_effect = list(frames)
        device = Mock()
        return BattleDriver(device, observer, order_change=True,
                            servant_index={316: {"name": "Oberon"}, 370: {"name": "Nitocris (Alter)"}}, **kwargs), device

    def test_back_line_and_availability_are_stamped_onto_tactical_frames(self):
        plain = Observation("t", "tactical", "now", allies=allies(), wave=1, turn=1, signals={})
        palette = Observation("p", "tactical", "now", allies=allies(), wave=1, turn=1,
                              signals={"master_palette": {"order_change": "ready"}})
        driver, _ = self.driver([plain, palette, member_screen("s"), Observation(
            "t2", "tactical", "now", allies=allies(), wave=1, turn=1, signals={})])
        first = driver.observe()
        self.assertFalse(first.signals["order_change_available"])
        driver.observe()
        driver.observe()
        later = driver.observe()
        self.assertTrue(later.signals["order_change_available"])
        self.assertEqual(316, later.signals["backline"][0]["servant_id"])
        self.assertIsNone(later.signals["backline"][2]["servant_id"], "an ambiguous name stays unresolved")

    def test_a_confirmed_swap_spends_it_and_moves_the_outgoing_servant_back(self):
        driver, _ = self.driver([])
        driver.backline = [dict(m) for m in BACKLINE]
        driver.order_change_state = "ready"
        driver.mark_order_change_used(1, 1, "Koyanskaya of Light")
        self.assertEqual("used", driver.order_change_state)
        self.assertEqual("Koyanskaya of Light", driver.backline[0]["name"])

    def test_disabled_order_change_adds_no_signals(self):
        observer = Mock()
        observer.observe.return_value = Observation("t", "tactical", "now", allies=allies(), wave=1, turn=1, signals={})
        driver = BattleDriver(Mock(), observer)
        self.assertNotIn("order_change_available", driver.observe().signals)

    def test_member_and_replace_taps_require_the_observed_screen(self):
        driver, device = self.driver([])
        driver.latest = Observation("t", "tactical", "now", signals={})
        for action in (Action("order_change_member", target=1), Action("order_change_confirm"),
                       Action("order_change_cancel")):
            with self.subTest(action.kind), self.assertRaises(ValueError):
                driver.send(action)
        driver.latest = member_screen("s", selected={1})
        with self.assertRaises(ValueError):
            driver.send(Action("order_change_confirm"))
        device.tap.assert_not_called()


class RefusedOrderChangeTests(unittest.TestCase):
    """Live 2026-09-24: a party with no back line got "You don't have any switchable
    Sub Members" and the run stalled on the open dialog."""

    def test_reconnaissance_closes_the_refusal_and_marks_order_change_unavailable(self):
        class Driver(ScriptedDriver):
            unavailable = False

            def mark_order_change_unavailable(self):
                Driver.unavailable = True
        refusal = Observation("refused", "skill_error", "now")
        driver = Driver([tactical("start"), tactical("palette", palette="ready"), refusal,
                         tactical("back", palette="ready"), tactical("closed")])
        control = BattleController(driver, sleeper=lambda _: None, max_polls=4)
        report = control.reconnoitre_order_change()
        self.assertEqual("unavailable", report["status"])
        self.assertTrue(Driver.unavailable)
        self.assertEqual([("master_menu", None, None), ("master_skill", 3, None),
                          ("dismiss_skill_error", None, None), ("master_menu", None, None)], driver.sent)

    def test_a_refused_swap_closes_the_dialog_and_is_rejected_unspent(self):
        """Live 2026-09-27: every sub member had come in as a reinforcement."""
        class Driver(ScriptedDriver):
            unavailable = False

            def mark_order_change_unavailable(self):
                Driver.unavailable = True
        refusal = Observation("refused", "skill_error", "now")
        driver = Driver([tactical("before"), tactical("palette", palette="ready"), refusal,
                         tactical("back", palette="ready"), tactical("closed")])
        control = BattleController(driver, sleeper=lambda _: None, max_polls=4)
        result = control.execute(SWAP)
        self.assertEqual("rejected", result.status, result.reason)
        self.assertTrue(Driver.unavailable)
        self.assertNotIn("order_change_confirm", [kind for kind, _, _ in driver.sent])
        self.assertIn(("dismiss_skill_error", None, None), driver.sent)

    def test_the_refusal_dialog_is_recognized_as_a_skill_error(self):
        import os, tempfile, cv2
        root = Path(__file__).resolve().parents[1]
        old = Path.cwd()
        os.chdir(root / "FGO-py")
        try:
            import fgoDetect
            from fgo_agent.observer import LegacyObserver
            with tempfile.TemporaryDirectory() as directory:
                state = LegacyObserver(Path(directory), detector_module=fgoDetect).observe(
                    cv2.imread(str(root / "tests/fixtures/order_change/no-sub-members.png")))
        finally:
            os.chdir(old)
        self.assertEqual("skill_error", state.phase)


class GrayedMasterSkillTests(unittest.TestCase):
    """Live 2026-09-25 (Tam Lin boss): the stage grays out Master Skill, the palette
    never opens, and the reconnaissance waited out its whole 90 s budget."""

    def test_an_unopened_palette_gives_up_quickly_and_disables_order_change(self):
        class Driver(ScriptedDriver):
            unavailable = False

            def mark_order_change_unavailable(self):
                Driver.unavailable = True
        clock = [0.0]

        def tick():
            clock[0] += 0.5
            return clock[0]
        driver = Driver(tactical(f"no-palette-{i}") for i in range(1000))
        control = BattleController(driver, sleeper=lambda _: None, max_polls=1000, timeout=None, monotonic=tick)
        report = control.reconnoitre_order_change(time_budget=90)
        self.assertEqual("unavailable", report["status"])
        self.assertTrue(Driver.unavailable)
        self.assertLess(clock[0], 20, "must not wait out the whole budget")
        self.assertEqual([("master_menu", None, None)], driver.sent)


class SameServantSwapTests(unittest.TestCase):
    """Live 2026-09-25 (Morgan): a nearly dead Arjuna (Alter) was swapped for a second
    Arjuna (Alter) from the back line. The slot's id never changed, so the verified
    swap was reported uncertain and the run stopped."""

    def frame(self, name, hp, np, ready, palette=None):
        obs = tactical(name, first=247, palette=palette)
        obs.allies[0].update(hp=hp, np=np, skills=[{"slot": i, "ready": ready} for i in (1, 2, 3)])
        obs.signals["backline"][0].update(name="Arjuna (Alter)", servant_id=247)
        return obs

    def test_a_copy_of_the_same_servant_is_confirmed_by_its_fresh_state(self):
        names = ["Arjuna (Alter)", "Koyanskaya of Light", "Arjuna (Alter)",
                 "Arjuna (Alter)", "Nitocris (Alter)", "Space Ereshkigal"]
        control, driver = controller([
            self.frame("before", 1200, 0, False), self.frame("palette", 1200, 0, False, palette="ready"),
            member_screen("screen", names=names), member_screen("one", selected={1}, names=names),
            member_screen("two", selected={1, 4}, replace=True, names=names),
            self.frame("after-1", 14487, 50, True), self.frame("after-2", 14487, 50, True)])
        result = control.execute(SWAP)
        self.assertEqual("confirmed", result.status, result.reason)
        self.assertIn("another copy", result.evidence[0])

    def test_an_unchanged_slot_is_still_uncertain(self):
        names = ["Arjuna (Alter)", "Koyanskaya of Light", "Arjuna (Alter)",
                 "Arjuna (Alter)", "Nitocris (Alter)", "Space Ereshkigal"]
        control, _ = controller([
            self.frame("before", 1200, 0, False), self.frame("palette", 1200, 0, False, palette="ready"),
            member_screen("screen", names=names), member_screen("one", selected={1}, names=names),
            member_screen("two", selected={1, 4}, replace=True, names=names),
            *[self.frame(f"same-{i}", 1200, 0, False) for i in range(4)]])
        self.assertEqual("uncertain", control.execute(SWAP).status)


class NoisyNameTests(unittest.TestCase):
    """Live 2026-09-25 (Morgan retry): "OberonW" in the reconnaissance, "Oberon XX" on the
    member screen; the swap backed out as a different member."""

    def test_stray_marks_at_a_names_edge_still_match(self):
        from fgo_agent.order_change import names_match
        self.assertTrue(names_match("Oberon XX", "OberonW"))
        self.assertTrue(names_match("YSpace Ereshkigal", "Space Ereshkigal"))

    def test_different_servants_still_differ(self):
        from fgo_agent.order_change import names_match
        self.assertFalse(names_match("Arjuna", "Arjuna (Alter)"))
        self.assertFalse(names_match("Altria", "Altria Caster"))
        self.assertFalse(names_match("Oberon", "Nitocris (Alter)"))


class ReinforcementTests(unittest.TestCase):
    """Live 2026-09-25 (Morgan): all three back-line members came in as reinforcements,
    yet Order Change was still offered with the stale back line and the game refused."""

    def frame(self, name, first, turn=1):
        return Observation(name, "tactical", "now", allies=allies(first), wave=1, turn=turn, signals={})

    def test_reinforcements_empty_the_back_line(self):
        frames = [self.frame("start", 314, 1),
                  self.frame("oberon-in", 316, 2), self.frame("oberon-held", 316, 2),
                  self.frame("nitocris-in", 370, 3), self.frame("nitocris-held", 370, 3),
                  self.frame("third-in", 999, 4), self.frame("third-held", 999, 4)]
        driver, _ = DriverOrderChangeTrackingTests.driver(frames)
        driver.backline = [dict(m) for m in BACKLINE]
        driver.order_change_state = "ready"
        states = [driver.observe() for _ in frames]
        self.assertEqual(3, len(states[1].signals["backline"]), "one frame is not yet a replacement")
        self.assertEqual(["Nitocris (Alter)", "Space Ereshkigal"], [m["name"] for m in states[2].signals["backline"]])
        # Live 2026-09-27: the game moves the remaining members up a place.
        self.assertEqual([1, 2], [m["slot"] for m in states[2].signals["backline"]])
        self.assertEqual([1], [m["slot"] for m in states[4].signals["backline"]])
        self.assertEqual(["Space Ereshkigal"], [m["name"] for m in states[4].signals["backline"]])
        self.assertEqual([], states[6].signals["backline"])
        self.assertFalse(states[6].signals["order_change_available"])

    def test_an_unidentified_servant_replaced_by_a_back_line_member_counts(self):
        # Live 2026-09-27: support Castoria (never identified) died and Zhuge Liang
        # came in; the back line kept him and Order Change met "no switchable".
        frames = [self.frame("start", None, 1), self.frame("zhuge-in", 999, 2), self.frame("zhuge-held", 999, 2)]
        driver, _ = DriverOrderChangeTrackingTests.driver(frames)
        driver.servant_index[999] = {"name": "Zhuge Liang (Lord El-Melloi II)"}
        driver.backline = [{"slot": 1, "name": "Zhuge Liang", "servant_id": None},
                           {"slot": 2, "name": "YSpace Ereshkigal", "servant_id": None}]
        driver.order_change_state = "ready"
        states = [driver.observe() for _ in frames]
        self.assertEqual(["YSpace Ereshkigal"], [m["name"] for m in states[2].signals["backline"]])
        self.assertTrue(driver._member_is({"name": "Zhuge LiangA"}, 999))
        self.assertFalse(driver._member_is({"name": "Space Ereshkigal"}, 999))

    def test_an_unidentified_servant_later_identified_is_not_a_reinforcement(self):
        frames = [self.frame("start", None, 1), self.frame("named", 316, 2), self.frame("named-2", 316, 2)]
        driver, _ = DriverOrderChangeTrackingTests.driver(frames)
        driver.backline = [{"slot": 1, "name": "Zhuge Liang", "servant_id": None}]
        driver.order_change_state = "ready"
        states = [driver.observe() for _ in frames]
        self.assertEqual(1, len(states[2].signals["backline"]))

    def test_a_misread_within_the_same_turn_is_not_a_reinforcement(self):
        # Live 2026-09-25 (Cathedral Guards): Lady Avalon flickered to Oberon during a skill.
        frames = [self.frame("start", 314), self.frame("flicker", 316), self.frame("flicker-2", 316),
                  self.frame("back", 314)]
        driver, _ = DriverOrderChangeTrackingTests.driver(frames)
        driver.backline = [dict(m) for m in BACKLINE]
        driver.order_change_state = "ready"
        states = [driver.observe() for _ in frames]
        self.assertEqual(3, len(states[-1].signals["backline"]))
        self.assertTrue(states[-1].signals["order_change_available"])

    def test_a_confirmed_swap_is_not_counted_as_a_reinforcement(self):
        frames = [self.frame("start", 314), self.frame("swapped", 316)]
        driver, _ = DriverOrderChangeTrackingTests.driver(frames)
        driver.backline = [dict(m) for m in BACKLINE]
        driver.order_change_state = "ready"
        driver.observe()
        driver.mark_order_change_used(1, 1, "Koyanskaya of Light")
        after = driver.observe()
        self.assertEqual(3, len(after.signals["backline"]))
