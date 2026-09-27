"""Enemy Status Changes: reading the dialog, deciding when to open it, and the
open-read-close sequence that must always end back on the tactical screen."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "FGO-py"))

from fgo_agent import Action, BattleController, Observation
from fgo_agent.driver import BattleDriver
from fgo_agent.planner import PlannerPolicy

FIXTURES = ROOT / "tests" / "fixtures" / "enemy_profile"


class DialogReadingTests(unittest.TestCase):
    """Live 2026-09-23: a swarm of Berserkers whose buffs inflict Poison and Curse."""

    @classmethod
    def setUpClass(cls):
        cls.old_cwd = Path.cwd()
        os.chdir(ROOT / "FGO-py")
        import fgoDetect
        cls.detector = fgoDetect

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls.old_cwd)

    def observe(self, name):
        import cv2
        from fgo_agent.observer import LegacyObserver
        with tempfile.TemporaryDirectory() as directory:
            return LegacyObserver(Path(directory), detector_module=self.detector).observe(
                cv2.imread(str(FIXTURES / name)))

    def test_the_dialog_is_its_own_phase_with_each_effect_read(self):
        state = self.observe("status-swarm-enemy1.png")
        self.assertEqual("enemy_profile", state.phase)
        profile = state.signals["enemy_profile"]
        self.assertEqual("Sub-Attribute: Earth", profile["sub_attribute"])
        titles = [effect["title"] for effect in profile["effects"]]
        self.assertEqual(3, len(titles), titles)
        self.assertEqual("Activate on Defeat (Inflict Poison & Curse)", titles[0])
        self.assertEqual("Activate when Damaged (Inflict Curse)", titles[1])
        self.assertIn("to a single enemy when attacked", profile["effects"][1]["text"])
        self.assertNotIn("SUPPORT", " ".join(e["title"] + e["text"] for e in profile["effects"]),
                         "labels behind the translucent panel must not leak into the text")

    def test_the_tactical_screen_is_not_mistaken_for_the_dialog(self):
        state = self.observe("tactical-swarm.png")
        self.assertEqual("tactical", state.phase)
        self.assertNotIn("enemy_profile", state.signals)


def enemy(slot, hp, present=True):
    return {"slot": slot, "hp": hp, "present": present, "charge": [0, 0]}


def tactical(name, enemies, *, wave=1, turn=1):
    return Observation(name, "tactical", "now", allies=[], enemies=enemies, wave=wave, turn=turn,
                       signals={"enemy_layout": "standard3"})


PROFILE = {"name": "???", "sub_attribute": "Sub-Attribute: Earth",
           "effects": [{"title": "Activate on Defeat", "text": "inflict Curse"}], "possibly_truncated": False}


def dialog(name):
    return Observation(name, "enemy_profile", "now", signals={"enemy_profile": dict(PROFILE)})


class FakeDevice:
    serial = "127.0.0.1:16384"

    def __init__(self):
        self.inputs = []

    def tap(self, x, y):
        self.inputs.append(("tap", x, y))

    def long_press(self, x, y, ms):
        self.inputs.append(("hold", x, y))


class Frames:
    def __init__(self, frames):
        self.frames = iter(frames)

    def observe(self, _image):
        return next(self.frames)


class SelectionTests(unittest.TestCase):
    def driver(self, frames):
        device = FakeDevice()
        device.screenshot = lambda: None
        return BattleDriver(device, Frames(frames), inspect_enemies=True), device

    def test_new_enemies_are_read_once_per_turn_and_again_when_hp_rises(self):
        driver, _ = self.driver([tactical("a", [enemy(1, 5000), enemy(2, None, present=None), enemy(3, 9000)])])
        first = driver.observe()
        self.assertEqual([1, 3], driver.enemies_to_inspect(first))
        for slot in (1, 3):
            driver.send(Action("enemy_profile_open", target=slot))
        self.assertEqual([], driver.enemies_to_inspect(first), "never twice in one turn")
        driver.enemy_profiles = {1: {"read_at": {"wave": 1, "turn": 1}}, 3: {"read_at": {"wave": 1, "turn": 1}}}
        later = tactical("b", [enemy(1, 3000), enemy(2, None, present=None), enemy(3, 250000)], turn=2)
        self.assertEqual([3], driver.enemies_to_inspect(later), "a refill or revive must be re-read")

    def test_disabled_or_unsupported_layouts_read_nothing(self):
        driver, _ = self.driver([])
        driver.inspect_enemies_enabled = False
        self.assertEqual([], driver.enemies_to_inspect(tactical("a", [enemy(1, 5000)])))
        driver.inspect_enemies_enabled = True
        six = tactical("b", [enemy(1, 5000)])
        six.signals["enemy_layout"] = "unsupported"
        self.assertEqual([], driver.enemies_to_inspect(six))

    def test_the_budget_bounds_inspections_per_battle(self):
        driver, _ = self.driver([])
        driver.inspections_left = 1
        self.assertEqual([1], driver.enemies_to_inspect(tactical("a", [enemy(1, 5000), enemy(2, 6000)])))

    def test_a_read_profile_is_stamped_onto_later_frames(self):
        driver, device = self.driver([tactical("a", [enemy(1, 5000)]), dialog("d"),
                                      tactical("b", [enemy(1, 5000)])])
        driver.observe()
        driver.send(Action("enemy_profile_open", target=1))
        self.assertEqual(("hold", 42, 40), device.inputs[-1])
        driver.observe()
        driver.send(Action("enemy_profile_close"))
        self.assertEqual(("tap", 1058, 117), device.inputs[-1])
        after = driver.observe()
        stamped = after.signals["enemy_profiles"]
        self.assertEqual(1, stamped[0]["slot"])
        self.assertEqual({"wave": 1, "turn": 1}, stamped[0]["read_at"])

    def test_opening_requires_a_present_enemy_on_the_tactical_screen(self):
        driver, _ = self.driver([tactical("a", [enemy(1, 5000), enemy(2, None, present=None)])])
        driver.observe()
        with self.assertRaises(ValueError):
            driver.send(Action("enemy_profile_open", target=2))
        with self.assertRaises(ValueError):
            driver.send(Action("enemy_profile_close"))


class ScriptedDriver:
    def __init__(self, frames, wanted):
        self.frames, self.wanted, self.sent = iter(frames), wanted, []

    def observe(self):
        return next(self.frames)

    def send(self, action):
        self.sent.append((action.kind, action.target))

    def enemies_to_inspect(self, observation):
        return list(self.wanted)


class ScoutSequenceTests(unittest.TestCase):
    def control(self, frames, wanted=(1,)):
        driver = ScriptedDriver(frames, wanted)
        return BattleController(driver, sleeper=lambda _: None, max_polls=4), driver

    def test_each_enemy_is_opened_read_and_closed_back_to_tactical(self):
        start = tactical("start", [enemy(1, 5000), enemy(2, 6000)])
        control, driver = self.control([dialog("d1"), tactical("t1", []), dialog("d2"), tactical("t2", [])],
                                       wanted=(1, 2))
        report = control.scout_enemies(start)
        self.assertEqual({"status": "ok", "read": [1, 2], "allies_read": [], "no_dialog": []}, report)
        self.assertEqual([("enemy_profile_open", 1), ("enemy_profile_close", None),
                          ("enemy_profile_open", 2), ("enemy_profile_close", None)], driver.sent)

    def test_nothing_to_read_sends_nothing(self):
        control, driver = self.control([], wanted=())
        self.assertIsNone(control.scout_enemies(tactical("start", [enemy(1, 5000)])))
        self.assertEqual([], driver.sent)

    def test_a_press_that_opens_nothing_is_recorded_not_retried(self):
        control, driver = self.control([tactical(f"t{i}", []) for i in range(4)])
        report = control.scout_enemies(tactical("start", [enemy(1, 5000)]))
        self.assertEqual({"status": "ok", "read": [], "allies_read": [], "no_dialog": [1]}, report)
        self.assertEqual([("enemy_profile_open", 1)], driver.sent)

    def test_a_dialog_that_will_not_close_is_uncertain(self):
        control, _ = self.control([dialog("d")] + [dialog(f"stuck{i}") for i in range(4)])
        report = control.scout_enemies(tactical("start", [enemy(1, 5000)]))
        self.assertEqual("uncertain", report["status"])

    def test_a_dialog_left_open_is_closed(self):
        control, driver = self.control([tactical("t", [])])
        self.assertEqual("ok", control.scout_enemies(dialog("left-open"))["status"])
        self.assertEqual([("enemy_profile_close", None)], driver.sent)


class PlannerViewTests(unittest.TestCase):
    def test_profiles_reach_the_request_marked_as_ocr_evidence(self):
        from test_agent_planner import Backend, choose, DATA, ally, obs
        policy = PlannerPolicy(DATA, Backend(choose("attack")))
        observation = obs("one", allies=[ally()], wave=1, turn=1)
        observation.signals["enemy_profiles"] = [{**PROFILE, "slot": 2, "read_at": {"wave": 1, "turn": 1}}]
        request = policy.build_request(observation)
        profile = request["enemy_profiles"][0]
        self.assertEqual(2, profile["slot"])
        self.assertEqual("Activate on Defeat", profile["effects"][0]["title"])
        self.assertIn("OCR", profile["source"])

    def test_no_profiles_means_no_field(self):
        from test_agent_planner import Backend, choose, DATA, ally, obs
        policy = PlannerPolicy(DATA, Backend(choose("attack")))
        self.assertNotIn("enemy_profiles", policy.build_request(obs("one", allies=[ally()], wave=1, turn=1)))


if __name__ == "__main__":
    unittest.main()


class NameLabelIdentityTests(unittest.TestCase):
    """Live 2026-09-23: a story guest Senji Muramasa showed an Alter Ego icon, FGO-py
    lists #302 as a Saber, so the portrait never matched and her skills vanished."""

    INDEX = {302: {"name": "Senji Muramasa", "class_name": "saber"},
             101: {"name": "Rama", "class_name": "saber"},
             127: {"name": "Leonardo da Vinci", "class_name": "caster"},
             253: {"name": "Leonardo da Vinci", "class_name": "rider"},
             284: {"name": "Altria Caster", "class_name": "caster"},
             2: {"name": "Altria Pendragon", "class_name": "saber"},
             3: {"name": "Altria Pendragon (Alter)", "class_name": "saber"},
             390: {"name": "Mélusine", "class_name": "ruler"}}

    def resolve(self, label, class_name):
        from fgo_agent.identity import resolve_battle_name
        return resolve_battle_name(label, class_name, self.INDEX)

    def test_short_and_truncated_labels_resolve_with_the_class_breaking_ties(self):
        self.assertEqual((253, "name_label_and_class"), self.resolve("Da Vinci", "rider"))
        self.assertEqual((284, "name_label_and_class"), self.resolve("Altria", "caster"))
        self.assertEqual((390, "name_label_and_class"), self.resolve(" Melusine Ondine", "ruler"))
        self.assertEqual((302, "name_label_class_differs"), self.resolve(" Senji Muramas", "alterEgo"),
                         "'Rama' must not match inside 'Muramas'")

    def test_ambiguity_stays_unidentified(self):
        self.assertEqual((None, None), self.resolve("Altria", "saber"))
        self.assertEqual((None, None), self.resolve("Da Vinci", "archer"))
        self.assertEqual((None, None), self.resolve("", "saber"))

    def test_the_live_guest_is_identified_from_its_name(self):
        state = DialogReadingTests.observe(DialogReadingTests, "tactical-swarm.png")
        first = next(a for a in state.allies if a["slot"] == 1)
        self.assertEqual((302, "name_label_class_differs"), (first["id"], first["identity_source"]))
        self.assertEqual(["portrait", "portrait"], [a["identity_source"] for a in state.allies[1:]])

    @classmethod
    def setUpClass(cls):
        DialogReadingTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        DialogReadingTests.tearDownClass()


class AllyStatusTests(unittest.TestCase):
    """Live 2026-09-23: a story fight opened with every card type, NP and skill sealed."""

    @classmethod
    def setUpClass(cls):
        DialogReadingTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        DialogReadingTests.tearDownClass()

    def test_the_status_tab_lists_each_seal_with_its_turns(self):
        state = DialogReadingTests.observe(DialogReadingTests, "ally-status-sealed.png")
        self.assertEqual("ally_status", state.phase)
        status = state.signals["ally_status"]
        self.assertEqual(("status", "Koyanskaya of Light", 13873), (status["tab"], status["name"], status["atk"]))
        self.assertEqual(["Quick Seal", "Arts Seal", "Buster Seal", "NP Seal", "Skill Seal"],
                         [e["title"] for e in status["effects"]])
        self.assertEqual({"2 Turn(s)"}, {e["duration"] for e in status["effects"]})

    def test_the_details_tab_is_recognized_so_status_can_be_selected(self):
        state = DialogReadingTests.observe(DialogReadingTests, "ally-details-tab.png")
        self.assertEqual(("ally_status", "details"), (state.phase, state.signals["ally_status"]["tab"]))
        self.assertNotIn("effects", state.signals["ally_status"])

    def test_enemy_and_tactical_frames_are_not_ally_dialogs(self):
        self.assertEqual("enemy_profile", DialogReadingTests.observe(DialogReadingTests, "status-swarm-enemy1.png").phase)
        self.assertEqual("tactical", DialogReadingTests.observe(DialogReadingTests, "tactical-swarm.png").phase)


def ally_dialog(name, tab):
    status = {"subject": "ally", "tab": tab, "name": "Koyanskaya of Light", "atk": 13873}
    if tab == "status":
        status["effects"] = [{"title": "Skill Seal", "text": "Unable to use skills", "duration": "2 Turn(s)"}]
    return Observation(name, "ally_status", "now", signals={"ally_status": status})


class AllyScoutTests(unittest.TestCase):
    def test_an_ally_opened_on_details_is_switched_to_status_then_closed(self):
        class Driver(ScriptedDriver):
            def allies_to_inspect(self, observation):
                return [1]
        driver = Driver([ally_dialog("d", "details"), ally_dialog("s", "status"), tactical("t", [])], ())
        control = BattleController(driver, sleeper=lambda _: None, max_polls=4)
        report = control.scout_enemies(tactical("start", [enemy(1, 5000)]))
        self.assertEqual([1], report["allies_read"])
        self.assertEqual([("ally_status_open", 1), ("ally_status_tab", None), ("ally_status_close", None)],
                         driver.sent)

    def test_statuses_are_read_once_and_again_only_when_the_icon_row_changes(self):
        device = FakeDevice()
        device.screenshot = lambda: None
        driver = BattleDriver(device, Frames([]), inspect_enemies=True)
        ally = {"slot": 1, "id": 314, "hp": 14000, "status_icons": [100] * 45}
        first = Observation("a", "tactical", "now", allies=[ally], wave=1, turn=1)
        self.assertEqual([1], driver.allies_to_inspect(first))
        driver.ally_statuses[1] = {"servant_id": 314, "icons_at_read": [100] * 45}
        same = Observation("b", "tactical", "now", allies=[ally], wave=1, turn=2)
        self.assertEqual([], driver.allies_to_inspect(same))
        changed = Observation("c", "tactical", "now", allies=[dict(ally, status_icons=[160] * 45)], wave=1, turn=3)
        self.assertEqual([1], driver.allies_to_inspect(changed))

    def test_ally_statuses_reach_the_request(self):
        from test_agent_planner import Backend, choose, DATA, ally, obs
        observation = obs("one", allies=[ally()], wave=1, turn=1)
        observation.signals["ally_statuses"] = [{"slot": 1, "servant_id": 10, "name": "K", "atk": 13873,
                                                 "effects": [{"title": "Skill Seal", "text": "t", "duration": "2 Turn(s)"}],
                                                 "read_at": {"wave": 1, "turn": 1}}]
        request = PlannerPolicy(DATA, Backend(choose("attack"))).build_request(observation)
        self.assertEqual("Skill Seal", request["ally_statuses"][0]["effects"][0]["title"])
        self.assertEqual(13873, request["ally_statuses"][0]["atk_observed"])


class LongListTests(unittest.TestCase):
    """Live 2026-09-24: a scrolling enemy list; the scrollbar merged every row and an
    empty OCR crop crashed the model, so the dialog was not recognized at all."""

    @classmethod
    def setUpClass(cls):
        DialogReadingTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        DialogReadingTests.tearDownClass()

    def test_each_effect_of_a_scrolling_list_is_read(self):
        state = DialogReadingTests.observe(DialogReadingTests, "status-wolf-regen.png")
        self.assertEqual("enemy_profile", state.phase)
        profile = state.signals["enemy_profile"]
        self.assertEqual(["Loyalty to the Queen", "King of the Fang", "Apply Trait (Roman)", "Damage Cut"],
                         [e["title"] for e in profile["effects"]])
        self.assertIn("restore HP at the end of", profile["effects"][0]["text"])
        self.assertTrue(profile["possibly_truncated"], "the list scrolls; more effects may be hidden")

    def test_one_failing_line_blanks_only_that_line(self):
        import numpy as np
        from fgo_agent.enemy_profile import _ocr
        def broken(crop):
            raise ValueError("model rejected the crop")
        self.assertEqual("", _ocr(np.zeros((20, 20, 3), np.uint8), 5, 15, 5, 15, broken))
        self.assertEqual("", _ocr(np.zeros((20, 20, 3), np.uint8), 30, 30, 5, 15, lambda c: "x"))


class ScrollTests(unittest.TestCase):
    """User report 2026-09-24: dialogs were opened but never scrolled, so long
    status lists were only partly read."""

    @classmethod
    def setUpClass(cls):
        DialogReadingTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        DialogReadingTests.tearDownClass()

    def test_the_scrollbar_says_whether_more_of_the_list_is_hidden(self):
        wolf = DialogReadingTests.observe(DialogReadingTests, "status-wolf-regen.png").signals["enemy_profile"]
        self.assertEqual((True, False), (wolf["scroll"]["scrollable"], wolf["scroll"]["at_end"]))
        swarm = DialogReadingTests.observe(DialogReadingTests, "status-swarm-enemy1.png").signals["enemy_profile"]
        self.assertFalse(swarm["scroll"]["scrollable"])
        sealed = DialogReadingTests.observe(DialogReadingTests, "ally-status-sealed.png").signals["ally_status"]
        self.assertFalse(sealed["scroll"]["scrollable"])

    def test_merging_a_scrolled_view_adds_only_new_effects(self):
        from fgo_agent.enemy_profile import merge_effects
        known = [{"title": "Loyalty to the Queen", "text": "restore HP at the end of the turn"},
                 {"title": "Damage Cut", "text": "Decrease damage taken", "duration": "2 Turn(s)"}]
        seen = [{"title": "restore HP at the end of the turn", "text": ""},   # cut-off tail of an old entry
                {"title": "Damage Cut", "text": "Decrease damage taken"},
                {"title": "Guts", "text": "Survive a fatal hit once", "duration": "1 Turn(s)"}]
        merged = merge_effects(known, seen)
        self.assertEqual(["Loyalty to the Queen", "Damage Cut", "Guts"], [e["title"] for e in merged])

    def test_the_controller_scrolls_until_the_end_then_closes(self):
        def view(name, thumb, at_end):
            return Observation(name, "enemy_profile", "now", signals={"enemy_profile": dict(
                PROFILE, scroll={"scrollable": True, "at_end": at_end, "thumb": thumb})})
        driver = ScriptedDriver([view("d1", [238, 470], False), view("d2", [300, 540], False),
                                 view("d3", [370, 605], True), tactical("t", [])], (1,))
        control = BattleController(driver, sleeper=lambda _: None, max_polls=4)
        control.scout_enemies(tactical("start", [enemy(1, 5000)]))
        self.assertEqual([("enemy_profile_open", 1), ("status_scroll", None), ("status_scroll", None),
                          ("enemy_profile_close", None)], driver.sent)

    def test_a_scrollbar_that_does_not_move_stops_scrolling(self):
        stuck = lambda name: Observation(name, "enemy_profile", "now", signals={"enemy_profile": dict(
            PROFILE, scroll={"scrollable": True, "at_end": False, "thumb": [238, 470]})})
        driver = ScriptedDriver([stuck("d1"), stuck("d2"), tactical("t", [])], (1,))
        control = BattleController(driver, sleeper=lambda _: None, max_polls=4)
        control.scout_enemies(tactical("start", [enemy(1, 5000)]))
        self.assertEqual(1, [kind for kind, _ in driver.sent].count("status_scroll"))

    def test_the_driver_merges_views_of_one_open_dialog(self):
        device = FakeDevice()
        device.screenshot = lambda: None
        device.swipe = lambda *a: device.inputs.append(("swipe",) + a)
        first = Observation("d1", "enemy_profile", "now", signals={"enemy_profile": dict(
            PROFILE, effects=[{"title": "A", "text": "one"}])})
        second = Observation("d2", "enemy_profile", "now", signals={"enemy_profile": dict(
            PROFILE, effects=[{"title": "B", "text": "two"}])})
        driver = BattleDriver(device, Frames([tactical("t", [enemy(1, 5000)]), first, second]), inspect_enemies=True)
        driver.observe()
        driver.send(Action("enemy_profile_open", target=1))
        driver.observe()
        driver.send(Action("status_scroll"))
        driver.observe()
        self.assertEqual(["A", "B"], [e["title"] for e in driver.enemy_profiles[1]["effects"]])


class LiveScrollTests(unittest.TestCase):
    """Live 2026-09-24: one swipe on the Black Wolf's list revealed effects the first
    view hid (Critical Up), and cut the top row under the header ("Damage cui")."""

    @classmethod
    def setUpClass(cls):
        DialogReadingTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        DialogReadingTests.tearDownClass()

    def test_two_live_views_merge_into_one_clean_list(self):
        from fgo_agent.enemy_profile import merge_effects
        top = DialogReadingTests.observe(DialogReadingTests, "status-wolf-scroll-top.png").signals["enemy_profile"]
        end = DialogReadingTests.observe(DialogReadingTests, "status-wolf-scroll-end.png").signals["enemy_profile"]
        self.assertEqual((False, True), (top["scroll"]["at_end"], end["scroll"]["at_end"]))
        titles = [e["title"] for e in merge_effects(top["effects"], end["effects"])]
        self.assertEqual(["Mors IlIness", "Damage Cut", "Activate when Attacking (Inflict Curse)", "ATK Up",
                          "Critical Up"], titles)


class AllyRereadCapTests(unittest.TestCase):
    """Re-reading on every icon change cost a quarter of a battle (2026-09-24)."""

    def test_a_changed_icon_row_is_reread_at_most_every_three_turns(self):
        device = FakeDevice()
        device.screenshot = lambda: None
        driver = BattleDriver(device, Frames([]), inspect_enemies=True)
        ally = {"slot": 1, "id": 314, "hp": 14000, "status_icons": [100] * 45}
        driver.ally_statuses[1] = {"servant_id": 314, "icons_at_read": [100] * 45, "read_at": {"wave": 1, "turn": 1}}
        changed = lambda turn, ident=314: Observation(f"t{turn}", "tactical", "now", wave=1, turn=turn,
                                                      allies=[dict(ally, id=ident, status_icons=[160] * 45)])
        self.assertEqual([], driver.allies_to_inspect(changed(2)))
        self.assertEqual([], driver.allies_to_inspect(changed(3)))
        self.assertEqual([1], driver.allies_to_inspect(changed(4)))
        self.assertEqual([1], driver.allies_to_inspect(changed(2, ident=316)), "a new servant is read at once")

    def test_an_enemy_np_lifts_the_cap_for_that_turn(self):
        device = FakeDevice()
        device.screenshot = lambda: None
        driver = BattleDriver(device, Frames([]), inspect_enemies=True)
        ally = {"slot": 1, "id": 314, "hp": 14000, "status_icons": [160] * 45}
        driver.ally_statuses[1] = {"servant_id": 314, "icons_at_read": [100] * 45, "read_at": {"wave": 1, "turn": 1}}
        frame = lambda turn, charge: Observation(f"t{turn}", "tactical", "now", wave=1, turn=turn, allies=[ally],
                                                 enemies=[{"slot": 1, "hp": 90000, "charge": list(charge)}])
        self.assertEqual([], driver.allies_to_inspect(frame(1, (5, 5))))
        self.assertEqual([1], driver.allies_to_inspect(frame(2, (0, 5))), "the boss just fired its NP")


class BossMechanicTests(unittest.TestCase):
    """Modelled on Olympus' Demeter: statuses on breaks and triggers, cycled self-buffs."""

    def driver(self, frames=()):
        device = FakeDevice()
        device.screenshot = lambda: None
        return BattleDriver(device, Frames(list(frames)), inspect_enemies=True)

    def test_a_triggered_infliction_in_an_enemy_profile_makes_rereads_every_turn(self):
        start = tactical("a", [enemy(1, 90000)])
        start.enemies[0]["status_icons"] = [100] * 42
        view = Observation("d", "enemy_profile", "now", signals={"enemy_profile": dict(PROFILE, effects=[
            {"title": "Activate when Attacking (Inflict Curse)", "text": "Low chance to inflict Curse"}])})
        driver = self.driver([start, view])
        driver.observe()
        driver.send(Action("enemy_profile_open", target=1))
        driver.observe()
        self.assertIn("inflicts statuses", driver.statuses_volatile)
        ally = {"slot": 1, "id": 314, "hp": 14000, "status_icons": [160] * 45}
        driver.ally_statuses[1] = {"servant_id": 314, "icons_at_read": [100] * 45, "read_at": {"wave": 1, "turn": 1}}
        self.assertEqual([1], driver.allies_to_inspect(Observation("t2", "tactical", "now", wave=1, turn=2, allies=[ally])))

    def test_a_new_debuff_on_an_ally_makes_the_battle_volatile(self):
        first = Observation("s1", "ally_status", "now", signals={"ally_status": {
            "tab": "status", "effects": [{"title": "ATK Up", "text": "x"}]}})
        second = Observation("s2", "ally_status", "now", signals={"ally_status": {
            "tab": "status", "effects": [{"title": "ATK Up", "text": "x"}, {"title": "Nullify Buff", "text": "y"}]}})
        ally = {"slot": 1, "id": 314, "hp": 14000}
        driver = self.driver([Observation("t1", "tactical", "now", wave=1, turn=1, allies=[ally]), first,
                              Observation("t4", "tactical", "now", wave=1, turn=4, allies=[ally]), second])
        driver.observe()
        driver.send(Action("ally_status_open", target=1))
        driver.observe()
        self.assertIsNone(driver.statuses_volatile)
        driver.observe()
        driver.send(Action("ally_status_open", target=1))
        driver.observe()
        self.assertIn("Nullify Buff", driver.statuses_volatile)

    def test_a_break_lifts_the_ally_reread_gap(self):
        driver = self.driver()
        ally = {"slot": 1, "id": 314, "hp": 14000, "status_icons": [160] * 45}
        driver.ally_statuses[1] = {"servant_id": 314, "icons_at_read": [100] * 45, "read_at": {"wave": 1, "turn": 1}}
        driver.enemy_profiles[1] = {"read_at": {"wave": 1, "turn": 1}}
        frame = lambda turn, hp: Observation(f"t{turn}", "tactical", "now", wave=1, turn=turn, allies=[ally],
                                             enemies=[enemy(1, hp)], signals={"enemy_layout": "standard3"})
        driver.enemies_to_inspect(frame(1, 5000))
        driver.enemies_to_inspect(frame(2, 270540))
        self.assertEqual([1], driver.allies_to_inspect(frame(2, 270540)))

    def test_a_cycled_enemy_buff_row_is_reread(self):
        driver = self.driver()
        driver.enemy_profiles[1] = {"read_at": {"wave": 1, "turn": 1}, "icons_at_read": [100] * 42}
        frame = lambda turn: Observation(f"t{turn}", "tactical", "now", wave=1, turn=turn,
                                         enemies=[dict(enemy(1, 90000), status_icons=[170] * 42)],
                                         signals={"enemy_layout": "standard3"})
        driver._enemy_last_hp[1] = 90000
        self.assertEqual([], driver.enemies_to_inspect(frame(2)))
        self.assertEqual([1], driver.enemies_to_inspect(frame(4)))
        driver.statuses_volatile = "boss"
        driver.enemy_profiles[1]["read_at"] = {"wave": 1, "turn": 4}
        driver._inspected_turn.clear()
        self.assertEqual([1], driver.enemies_to_inspect(frame(5)))
