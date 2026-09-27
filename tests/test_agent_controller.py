import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "FGO-py"))

from fgo_agent import Action, BattleController, Observation
from fgo_agent.controller import UNOBSERVED_READINESS


def observation(identifier, phase="tactical", *, allies=None, cards=None, enemies=None, **kwargs):
    return Observation(identifier, phase, "2026-09-21T00:00:00Z", allies=allies or [],
                       cards=cards or [], enemies=enemies or [], **kwargs)


def ready_ally(cooldown=0, ready=True, ident=10, hp=100, np=0):
    return [{"slot": 1, "id": ident, "hp": hp, "np": np,
             "skills": [{"slot": 1, "ready": ready, "cooldown": cooldown}]}]


class FakeDriver:
    def __init__(self, observations):
        self.observations = iter(observations)
        self.sent = []

    def observe(self):
        item = next(self.observations)
        if isinstance(item, Exception):
            raise item
        return item

    def send(self, action):
        self.sent.append(action)


class TargetAwareFakeDriver(FakeDriver):
    def __init__(self, observations, target_compatibility):
        super().__init__(observations)
        self.target_compatibility = iter(target_compatibility)
        self.checked_targets = []

    def can_target(self, target):
        self.checked_targets.append(target)
        return next(self.target_compatibility)


class DeadlineTargetDriver(FakeDriver):
    def __init__(self, observations, clock):
        super().__init__(observations)
        self.clock = clock

    def observe(self):
        item = super().observe()
        if item.phase == "target_selection":
            self.clock.value += 1
        return item


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class BattleControllerTests(unittest.TestCase):
    def test_policy_decision_is_rejected_after_turn_changes_without_input(self):
        expected = observation("plan", allies=ready_ally(), wave=1, turn=1)
        controller = self.controller([observation("fresh", allies=ready_ally(), wave=1, turn=2)])
        result = controller.execute(Action("attack"), expected=expected)
        self.assertEqual(result.status, "rejected")
        self.assertEqual(controller.driver.sent, [])

    def test_policy_skill_is_rejected_when_actor_identity_becomes_unknown(self):
        expected = observation("plan", allies=ready_ally(), wave=1, turn=1)
        controller = self.controller([observation("fresh", allies=ready_ally(ident=None), wave=1, turn=1)])
        result = controller.execute(Action("skill", servant=1, skill=1), expected=expected)
        self.assertEqual(result.status, "rejected")
        self.assertEqual(controller.driver.sent, [])

    def controller(self, observations, **kwargs):
        clock = FakeClock()
        return BattleController(FakeDriver(observations), monotonic=clock, sleeper=clock.sleep,
                                poll_interval=1, **kwargs)

    def test_skill_is_sent_once_when_icon_becomes_dark_without_cooldown_evidence(self):
        controller = self.controller([
            observation("before", allies=ready_ally(), wave=1, turn=1),
            observation("dark", allies=ready_ally(ready=False)),
            observation("still-dark", allies=ready_ally(ready=False)),
        ], max_polls=2)
        result = controller.execute(Action("skill", servant=1, skill=1))
        self.assertEqual("uncertain", result.status)
        self.assertIsNone(result.skill_consumed)
        self.assertEqual(["skill was unavailable in two consecutive tactical frames"], result.evidence)
        self.assertEqual(1, len(controller.driver.sent))

    def test_unknown_actor_frame_does_not_end_cooldown_confirmation_early(self):
        controller = self.controller([
            observation("before", allies=ready_ally(), wave=1, turn=1),
            observation("obscured", allies=ready_ally(cooldown=5, ready=False, ident=None), wave=1, turn=1),
            observation("known", allies=ready_ally(cooldown=5, ready=False), wave=1, turn=1),
            observation("stable", allies=ready_ally(cooldown=5, ready=False), wave=1, turn=1),
        ], max_polls=3)
        result = controller.execute(Action("skill", servant=1, skill=1))
        self.assertEqual("confirmed", result.status)
        self.assertEqual("stable", result.after_observation_id)
        self.assertTrue(result.skill_consumed)
        self.assertEqual(1, len(controller.driver.sent))

    def test_unknown_actor_cannot_confirm_even_with_repeated_cooldown(self):
        controller = self.controller([
            observation("before", allies=ready_ally(), wave=1, turn=1),
            observation("unknown1", allies=ready_ally(cooldown=5, ready=False, ident=None), wave=1, turn=1),
            observation("unknown2", allies=ready_ally(cooldown=5, ready=False, ident=None), wave=1, turn=1),
            observation("unknown3", allies=ready_ally(cooldown=5, ready=False, ident=None), wave=1, turn=1),
        ], max_polls=3)
        result = controller.execute(Action("skill", servant=1, skill=1))
        self.assertEqual("uncertain", result.status)
        self.assertEqual("unknown3", result.after_observation_id)
        self.assertIsNone(result.skill_consumed)
        self.assertEqual(1, len(controller.driver.sent))

    def test_a_one_frame_identity_flicker_does_not_void_the_cast(self):
        # Live 2026-09-25 (Lady Avalon, Kama): the portrait read as another servant
        # for one frame of the skill animation and the cast was reported uncertain.
        controller = self.controller([
            observation("before", allies=ready_ally(), wave=1, turn=1),
            observation("flicker", allies=ready_ally(cooldown=5, ready=False, ident=316), wave=1, turn=1),
            observation("after", allies=ready_ally(cooldown=5, ready=False), wave=1, turn=1),
            observation("stable", allies=ready_ally(cooldown=5, ready=False), wave=1, turn=1),
        ], max_polls=4)
        result = controller.execute(Action("skill", servant=1, skill=1))
        self.assertEqual("confirmed", result.status, result.reason)

    def test_a_lasting_identity_change_is_still_uncertain(self):
        controller = self.controller([
            observation("before", allies=ready_ally(), wave=1, turn=1),
            observation("other", allies=ready_ally(cooldown=5, ready=False, ident=316), wave=1, turn=1),
            observation("other-2", allies=ready_ally(cooldown=5, ready=False, ident=316), wave=1, turn=1),
        ], max_polls=3)
        result = controller.execute(Action("skill", servant=1, skill=1))
        self.assertEqual("uncertain", result.status)
        self.assertEqual("servant identity changed after skill attempt", result.reason)

    def test_skill_confirms_only_when_cooldown_increases(self):
        controller = self.controller([
            observation("before", allies=ready_ally(cooldown=None), wave=1, turn=1),
            observation("after", allies=ready_ally(cooldown=5, ready=False, hp=125), wave=1, turn=1),
            observation("stable", allies=ready_ally(cooldown=5, ready=False, hp=125), wave=1, turn=1),
        ])
        result = controller.execute(Action("skill", servant=1, skill=1))
        self.assertEqual("confirmed", result.status)
        self.assertTrue(result.skill_consumed)
        self.assertTrue(result.effect_observed)
        self.assertEqual("same increased cooldown observed in two consecutive tactical frames", result.evidence[0])
        self.assertIn("cast attribution is not guaranteed", result.evidence[1])

    def test_targeted_skill_sends_one_internal_target_action(self):
        controller = self.controller([
            observation("before", allies=ready_ally(), wave=1, turn=1),
            observation("targets", "target_selection", target_count=2, allies=ready_ally(), wave=1, turn=1),
            observation("after", allies=ready_ally(cooldown=4, ready=False), wave=1, turn=1),
            observation("stable", allies=ready_ally(cooldown=4, ready=False), wave=1, turn=1),
        ])
        result = controller.execute(Action("skill", servant=1, skill=1, target=3))
        self.assertEqual("confirmed", result.status)
        self.assertEqual(["skill", "skill_target"], [item.kind for item in controller.driver.sent])
        self.assertEqual(3, controller.driver.sent[1].target)

    def test_execution_deadline_prevents_later_target_input_without_retry(self):
        clock = FakeClock()
        driver = DeadlineTargetDriver([
            observation("before", allies=ready_ally()),
            observation("targets", "target_selection", target_count=1),
        ], clock)
        controller = BattleController(driver, monotonic=clock, sleeper=clock.sleep)
        result = controller.execute(Action("skill", servant=1, skill=1, target=1), time_budget=1)
        self.assertEqual("uncertain", result.status)
        self.assertIn("before target input", result.reason)
        self.assertEqual(["skill"], [item.kind for item in driver.sent])

    def test_exhausted_execution_budget_does_not_observe_or_send(self):
        controller = self.controller([observation("before")])
        result = controller.execute(Action("attack"), time_budget=0)
        self.assertEqual("uncertain", result.status)
        self.assertEqual([], controller.driver.sent)

    def test_unknown_animation_frame_does_not_imply_replacement_or_end_skill_polling(self):
        controller = self.controller([
            observation("before", allies=ready_ally(cooldown=None), wave=1, turn=1),
            observation("animation", "unknown"),
            observation("after", allies=ready_ally(cooldown=4, ready=False), wave=1, turn=1),
            observation("stable", allies=ready_ally(cooldown=4, ready=False), wave=1, turn=1),
        ], max_polls=4)
        result = controller.execute(Action("skill", servant=1, skill=1))
        self.assertEqual("confirmed", result.status)
        self.assertEqual("stable", result.after_observation_id)

    def test_cooldown_requires_known_same_actor_and_turn_wave_context(self):
        controller = self.controller([
            observation("before", allies=ready_ally(cooldown=None)),
            observation("after", allies=ready_ally(cooldown=4, ready=False)),
        ], max_polls=1)
        result = controller.execute(Action("skill", servant=1, skill=1))
        self.assertEqual("uncertain", result.status)
        self.assertIsNone(result.skill_consumed)

    def test_targeted_skill_refuses_ambiguous_target_count_without_target_send(self):
        controller = self.controller([
            observation("before", allies=ready_ally()),
            observation("targets", "target_selection", target_count=None, allies=ready_ally()),
        ])
        result = controller.execute(Action("skill", servant=1, skill=1, target=1))
        self.assertEqual("uncertain", result.status)
        self.assertEqual(["skill"], [item.kind for item in controller.driver.sent])

    def test_targeted_skill_waits_for_compatible_dialog_before_targeting(self):
        clock = FakeClock()
        driver = TargetAwareFakeDriver([
            observation("before", allies=ready_ally(), wave=1, turn=1),
            observation("mismatch", "target_selection", target_count=2, wave=1, turn=1),
            observation("compatible", "target_selection", target_count=2, wave=1, turn=1),
            observation("after", allies=ready_ally(cooldown=4, ready=False), wave=1, turn=1),
            observation("stable", allies=ready_ally(cooldown=4, ready=False), wave=1, turn=1),
        ], [False, True])
        controller = BattleController(driver, max_polls=4, monotonic=clock, sleeper=clock.sleep, poll_interval=1)
        result = controller.execute(Action("skill", servant=1, skill=1, target=1))
        self.assertEqual("confirmed", result.status)
        self.assertEqual(["skill", "skill_target"], [item.kind for item in driver.sent])
        self.assertEqual([1, 1], driver.checked_targets)

    def test_targeted_skill_times_out_without_tapping_persistently_incompatible_dialog(self):
        clock = FakeClock()
        driver = TargetAwareFakeDriver([
            observation("before", allies=ready_ally()),
            observation("mismatch-1", "target_selection", target_count=2),
            observation("mismatch-2", "target_selection", target_count=2),
        ], [False, False])
        controller = BattleController(driver, max_polls=2, monotonic=clock, sleeper=clock.sleep, poll_interval=1)
        result = controller.execute(Action("skill", servant=1, skill=1, target=1))
        self.assertEqual("uncertain", result.status)
        self.assertEqual(["skill"], [item.kind for item in driver.sent])
        self.assertIn("compatible", result.reason)

    def test_mismatched_target_dialog_cannot_confirm_from_later_tactical_cooldown(self):
        clock = FakeClock()
        driver = TargetAwareFakeDriver([
            observation("before", allies=ready_ally(cooldown=None), wave=1, turn=1),
            observation("mismatch", "target_selection", target_count=2, wave=1, turn=1),
            observation("after", allies=ready_ally(cooldown=4, ready=False), wave=1, turn=1),
            observation("stable", allies=ready_ally(cooldown=4, ready=False), wave=1, turn=1),
        ], [False])
        controller = BattleController(driver, max_polls=3, monotonic=clock, sleeper=clock.sleep, poll_interval=1)
        result = controller.execute(Action("skill", servant=1, skill=1, target=1))
        self.assertEqual("uncertain", result.status)
        self.assertEqual(["skill"], [item.kind for item in driver.sent])
        self.assertIn("target dialog", result.reason)

    def test_unknown_phase_and_bad_cards_are_rejected_without_actions(self):
        unknown = self.controller([observation("unknown", "unknown")])
        rejected = unknown.execute(Action("attack"))
        self.assertEqual("rejected", rejected.status)
        self.assertEqual([], unknown.driver.sent)
        cards = self.controller([observation("cards", "cards", cards=[{"slot": 1, "type": "Buster", "sealed": None}])])
        rejected_cards = cards.execute(Action("cards", cards=(1, 2, 3)))
        self.assertEqual("rejected", rejected_cards.status)
        self.assertEqual([], cards.driver.sent)

    def test_enemy_target_requires_known_positive_hp_and_verifiability_signal(self):
        for hp in (None, 0):
            controller = self.controller([observation(
                "tactical", enemies=[{"slot": 1, "hp": hp}],
                signals={"enemy_target_verifiable": True},
            )])
            result = controller.execute(Action("enemy_target", target=1))
            self.assertEqual("rejected", result.status)
            self.assertEqual([], controller.driver.sent)

        unverifiable = self.controller([observation("tactical", enemies=[{"slot": 1, "hp": 100}])])
        result = unverifiable.execute(Action("enemy_target", target=1))
        self.assertEqual("rejected", result.status)
        self.assertEqual([], unverifiable.driver.sent)

    def test_enemy_target_rejects_stale_or_sparse_preflight_without_input(self):
        expected = observation("plan", wave=1, turn=1, enemies=[{"slot": 1, "hp": 100}, {"slot": 2, "hp": 100}],
                               signals={"enemy_layout": "standard3", "enemy_target_verifiable": True,
                                        "selected_enemy": 1})
        stale = self.controller([observation("fresh", wave=1, turn=2, enemies=[{"slot": 1, "hp": 100}, {"slot": 2, "hp": 100}],
                                            signals={"enemy_layout": "standard3", "enemy_target_verifiable": True,
                                                     "selected_enemy": 1})])
        self.assertEqual("rejected", stale.execute(Action("enemy_target", target=2), expected=expected).status)
        sparse = self.controller([observation("fresh", wave=1, turn=1, enemies=[{"slot": 2, "hp": 0}],
                                             signals={"enemy_layout": "standard3", "enemy_target_verifiable": True,
                                                      "selected_enemy": 1})])
        self.assertEqual("rejected", sparse.execute(Action("enemy_target", target=2)).status)
        self.assertEqual([], stale.driver.sent)
        self.assertEqual([], sparse.driver.sent)

    def test_enemy_target_needs_two_matching_tactical_frames(self):
        signals = {"enemy_layout": "standard3", "enemy_target_verifiable": True, "selected_enemy": 1}
        controller = self.controller([
            observation("before", wave=1, turn=1, enemies=[{"slot": 1, "hp": 100}, {"slot": 2, "hp": 100}], signals=signals),
            observation("one", wave=1, turn=1, enemies=[{"slot": 1, "hp": 100}, {"slot": 2, "hp": 100}],
                        signals={**signals, "selected_enemy": 2}),
        ], max_polls=1)
        result = controller.execute(Action("enemy_target", target=2))
        self.assertEqual("uncertain", result.status)
        self.assertEqual([Action("enemy_target", target=2)], controller.driver.sent)

    def test_enemy_target_confirms_two_matching_frames_and_rejects_wrong_marker(self):
        base = {"enemy_layout": "standard3", "enemy_target_verifiable": True, "selected_enemy": 1}
        success = self.controller([
            observation("before", wave=1, turn=1, enemies=[{"slot": 1, "hp": 100}, {"slot": 2, "hp": 100}], signals=base),
            observation("first", wave=1, turn=1, enemies=[{"slot": 1, "hp": 100}, {"slot": 2, "hp": 100}], signals={**base, "selected_enemy": 2}),
            observation("second", wave=1, turn=1, enemies=[{"slot": 1, "hp": 100}, {"slot": 2, "hp": 100}], signals={**base, "selected_enemy": 2}),
        ], max_polls=2)
        self.assertEqual("confirmed", success.execute(Action("enemy_target", target=2)).status)
        wrong = self.controller([
            observation("before", wave=1, turn=1, enemies=[{"slot": 1, "hp": 100}, {"slot": 2, "hp": 100}], signals=base),
            observation("wrong", wave=1, turn=1, enemies=[{"slot": 1, "hp": 100}, {"slot": 2, "hp": 100}, {"slot": 3, "hp": 100}], signals={**base, "selected_enemy": 3}),
        ])
        self.assertEqual("uncertain", wrong.execute(Action("enemy_target", target=2)).status)
        self.assertEqual(1, len(wrong.driver.sent))

    def test_enemy_target_deadline_prevents_input(self):
        controller = self.controller([observation("before", wave=1, turn=1, enemies=[{"slot": 1, "hp": 100}, {"slot": 2, "hp": 100}],
                                                 signals={"enemy_layout": "standard3", "enemy_target_verifiable": True,
                                                          "selected_enemy": 1})])
        result = controller.execute(Action("enemy_target", target=2), time_budget=0)
        self.assertEqual("uncertain", result.status)
        self.assertEqual([], controller.driver.sent)

    def test_enemy_target_requires_positive_context_selected_hp_and_unambiguous_slots(self):
        signals = {"enemy_layout": "standard3", "enemy_target_verifiable": True, "selected_enemy": 1}
        cases = [
            (0, 1, [{"slot": 1, "hp": 100}, {"slot": 2, "hp": 100}]),
            (1, -1, [{"slot": 1, "hp": 100}, {"slot": 2, "hp": 100}]),
            (1, 1, [{"slot": 1, "hp": 0}, {"slot": 2, "hp": 100}]),
            (1, 1, [{"slot": 1, "hp": 100}, {"slot": 2, "hp": 100}, {"slot": 2, "hp": 100}]),
            (1, 1, [{"slot": True, "hp": 100}, {"slot": 2, "hp": 100}]),
        ]
        for wave, turn, enemies in cases:
            controller = self.controller([observation("before", wave=wave, turn=turn, enemies=enemies, signals=signals)])
            self.assertEqual("rejected", controller.execute(Action("enemy_target", target=2)).status)
            self.assertEqual([], controller.driver.sent)

    def test_enemy_target_tolerates_transient_unknown_observation_by_resetting_confirmation(self):
        base = {"enemy_layout": "standard3", "enemy_target_verifiable": True, "selected_enemy": 1}
        enemies = [{"slot": 1, "hp": 100}, {"slot": 2, "hp": 100}]
        controller = self.controller([
            observation("before", wave=1, turn=1, enemies=enemies, signals=base),
            observation("unknown", wave=1, turn=1, enemies=enemies, signals={**base, "enemy_target_verifiable": False, "selected_enemy": None}),
            observation("first", wave=1, turn=1, enemies=enemies, signals={**base, "selected_enemy": 2}),
            observation("second", wave=1, turn=1, enemies=enemies, signals={**base, "selected_enemy": 2}),
        ], max_polls=3)
        self.assertEqual("confirmed", controller.execute(Action("enemy_target", target=2)).status)

    def test_attack_timeout_is_bounded_by_injected_clock(self):
        controller = self.controller([
            observation("before"), observation("still"), observation("still-2"),
        ], max_polls=3, timeout=1.0)
        result = controller.execute(Action("attack"))
        self.assertEqual("uncertain", result.status)
        self.assertEqual("still", result.after_observation_id)
        self.assertEqual(1, len(controller.driver.sent))

    def test_cards_need_positive_completion_evidence(self):
        available = [
            {"slot": 1, "type": "Buster", "sealed": False},
            {"slot": 2, "type": "Arts", "sealed": False},
            {"slot": 3, "type": "NP", "sealed": False, "ready": True},
        ]
        controller = self.controller([
            observation("before", "cards", cards=available),
            observation("animation", "unknown"),
            observation("done", "tactical", signals={"turn_advanced": True}),
        ])
        result = controller.execute(Action("cards", cards=(1, 2, 3)))
        self.assertEqual("confirmed", result.status)
        self.assertEqual("done", result.after_observation_id)

    def test_a_card_screen_that_stays_after_the_picks_goes_back(self):
        """Live 2026-09-27: sealed NPs were tapped, the game waited for more cards."""
        available = [{"slot": s, "type": "Arts", "sealed": False} for s in (1, 2, 3)]
        stuck = [observation(f"stuck-{i}", "cards", cards=available) for i in range(40)]
        controller = self.controller([observation("before", "cards", cards=available), *stuck,
                                      observation("back", "tactical")], max_polls=200, timeout=200)
        result = controller.execute(Action("cards", cards=(1, 2, 3)))
        self.assertEqual("rejected", result.status)
        self.assertIn("did not attack", result.reason)
        self.assertEqual(["cards", "cards_back"], [a.kind for a in controller.driver.sent])

    def test_animation_frames_get_speed_taps_only_when_enabled(self):
        """User tip 2026-09-27: a tap during a skill or NP animation speeds it up."""
        available = [{"slot": s, "type": "Arts", "sealed": False} for s in (1, 2, 3)]
        frames = lambda: [observation("before", "cards", cards=available),
                          *[observation(f"anim-{i}", "unknown") for i in range(6)],
                          observation("done", "tactical", signals={"turn_advanced": True})]
        plain = self.controller(frames(), max_polls=50, timeout=100)
        plain.execute(Action("cards", cards=(1, 2, 3)))
        self.assertNotIn("speed_tap", [a.kind for a in plain.driver.sent])
        clock = FakeClock()
        fast = BattleController(FakeDriver(frames()), monotonic=clock, sleeper=clock.sleep, poll_interval=1,
                                max_polls=50, timeout=100, speed_taps=True)
        result = fast.execute(Action("cards", cards=(1, 2, 3)))
        self.assertEqual("confirmed", result.status)
        taps = [a.kind for a in fast.driver.sent].count("speed_tap")
        self.assertEqual(3, taps, "one tap every 2 s over six 1 s animation frames")

    def test_a_single_transition_frame_after_a_skill_tap_gets_no_speed_tap(self):
        """Live 2026-09-27: that frame led into target selection; a tap cancelled it."""
        def run(frames):
            clock = FakeClock()
            control = BattleController(FakeDriver(frames), monotonic=clock, sleeper=clock.sleep, poll_interval=1,
                                       max_polls=6, timeout=100, speed_taps=True)
            control.execute(Action("skill", servant=1, skill=1))
            return [a.kind for a in control.driver.sent].count("speed_tap")
        start = observation("before", allies=ready_ally(), wave=1, turn=1)
        after = [observation(f"cool-{i}", allies=ready_ally(cooldown=5, ready=False), wave=1, turn=1) for i in range(3)]
        self.assertEqual(0, run([start, observation("transition", "unknown"), *after]))
        self.assertEqual(1, run([start, observation("anim-1", "unknown"), observation("anim-2", "unknown"),
                                 observation("anim-3", "unknown"), *after]))

    def test_run_stops_after_a_rejected_action(self):
        controller = self.controller([observation("unknown", "unknown")])
        results = controller.run([Action("attack"), Action("attack")])
        self.assertEqual(1, len(results))
        self.assertEqual("rejected", results[0].status)

    def test_initial_and_polled_observation_failures_return_error_results(self):
        initial = self.controller([RuntimeError("capture unavailable")])
        initial_result = initial.execute(Action("attack"))
        self.assertEqual("error", initial_result.status)
        self.assertIsNone(initial_result.before_observation_id)
        self.assertIsNone(initial_result.skill_consumed)

        polled = self.controller([observation("before", allies=ready_ally()), RuntimeError("frame read failed")])
        polled_result = polled.execute(Action("skill", servant=1, skill=1))
        self.assertEqual("error", polled_result.status)
        self.assertEqual("before", polled_result.before_observation_id)
        self.assertIsNone(polled_result.skill_consumed)


if __name__ == "__main__":
    unittest.main()


class UnreadableSkillReadinessTests(unittest.TestCase):
    """An unreadable readiness icon must not act as an observed cooldown.

    This is the deadlock seen live on 2026-09-22: a policy proposed a skill, the
    controller re-read the frame, readiness was unreadable, and the action was
    refused as "not observably ready" every single attempt.
    """

    @staticmethod
    def _ally(ready):
        return {"slot": 1, "id": 10, "hp": 100, "np": 0,
                "skills": [{"slot": 1, "ready": ready, "cooldown": None}]}

    def _observation(self, ready, identifier="o"):
        return Observation(identifier, "tactical", "now", allies=[self._ally(ready)], wave=1, turn=1)

    def test_readiness_that_reappears_is_accepted_rather_than_refused(self):
        frames = [self._observation(None, "unreadable"), self._observation(None, "still-unreadable"),
                  self._observation(True, "readable")]
        driver = FakeDriver(list(frames) + [Observation("after", "tactical", "now",
                                                        allies=[self._ally(False)], wave=1, turn=1)] * 4)
        controller = BattleController(driver, sleeper=lambda _: None)
        result = controller.execute(Action("skill", servant=1, skill=1, target=1))
        self.assertNotEqual("rejected", result.status, result.reason)
        self.assertTrue(driver.sent, "the skill must actually be sent once readiness is observed")

    def test_readiness_observed_false_is_still_refused_immediately(self):
        driver = FakeDriver([self._observation(False)])
        controller = BattleController(driver, sleeper=lambda _: None)
        result = controller.execute(Action("skill", servant=1, skill=1, target=1))
        self.assertEqual("rejected", result.status)
        self.assertEqual("skill is not observably ready", result.reason)
        self.assertFalse(driver.sent, "an observed cooldown must never send input")

    def test_permanently_unreadable_readiness_is_refused_with_its_own_reason(self):
        driver = FakeDriver([self._observation(None) for _ in range(8)])
        controller = BattleController(driver, sleeper=lambda _: None)
        result = controller.execute(Action("skill", servant=1, skill=1, target=1))
        self.assertEqual("rejected", result.status)
        self.assertEqual(UNOBSERVED_READINESS, result.reason)
        self.assertFalse(driver.sent)


class StableDarknessConfirmationTests(unittest.TestCase):
    """A cast whose cooldown numeral cannot be read must not halt the battle.

    Live on 2026-09-22 (Ember Extreme, run 20260922T211159Z-d5d04ec1) a targeted
    charge skill landed -- the target's NP rose 0 -> 32 and its cooldowns fell by
    two -- but the used skill's cooldown read as an impossible 0, so confirmation
    waited for a readable increase until timeout and the runner stopped.
    """

    @staticmethod
    def _party(used_ready, used_cooldown=None, target_np=0, other_ready=True):
        return [
            {"slot": 1, "id": 314, "hp": 15000, "np": 0,
             "skills": [{"slot": 1, "ready": used_ready, "cooldown": used_cooldown},
                        {"slot": 2, "ready": other_ready, "cooldown": None}]},
            {"slot": 3, "id": 247, "hp": 14000, "np": target_np,
             "skills": [{"slot": 1, "ready": other_ready, "cooldown": None}]},
        ]

    def _frame(self, name, **party):
        return observation(name, allies=self._party(**party), wave=2, turn=2)

    def test_targeted_cast_with_unreadable_cooldown_is_confirmed(self):
        frames = [self._frame("before", used_ready=True),
                  observation("dialog", "target_selection", target_count=3),
                  self._frame("dark-1", used_ready=False, target_np=32),
                  self._frame("dark-2", used_ready=False, target_np=32)]
        driver = FakeDriver(frames)
        controller = BattleController(driver, sleeper=lambda _: None, max_polls=10)
        result = controller.execute(Action("skill", servant=1, skill=1, target=3))
        self.assertEqual("confirmed", result.status, result.reason)
        self.assertTrue(result.skill_consumed)
        self.assertEqual(["skill", "skill_target"], [a.kind for a in driver.sent],
                         "exactly one skill tap and one target tap, never a repeat")

    def test_uncorroborated_darkness_needs_three_specific_frames(self):
        two = [self._frame("before", used_ready=True),
               self._frame("dark-1", used_ready=False), self._frame("dark-2", used_ready=False)]
        result = BattleController(FakeDriver(two), sleeper=lambda _: None, max_polls=2).execute(
            Action("skill", servant=1, skill=1))
        self.assertEqual("uncertain", result.status)
        three = two + [self._frame("dark-3", used_ready=False)]
        result = BattleController(FakeDriver(three), sleeper=lambda _: None, max_polls=3).execute(
            Action("skill", servant=1, skill=1))
        self.assertEqual("confirmed", result.status, result.reason)

    def test_globally_dimmed_row_never_confirms_without_corroboration(self):
        frames = [self._frame("before", used_ready=True)] + [
            self._frame(f"dim-{i}", used_ready=False, other_ready=False) for i in range(5)]
        result = BattleController(FakeDriver(frames), sleeper=lambda _: None, max_polls=5).execute(
            Action("skill", servant=1, skill=1))
        self.assertEqual("uncertain", result.status)
        self.assertIsNone(result.skill_consumed)

    def test_darkness_across_a_turn_change_does_not_confirm(self):
        frames = [self._frame("before", used_ready=True),
                  observation("next-turn-1", allies=self._party(used_ready=False), wave=2, turn=3),
                  observation("next-turn-2", allies=self._party(used_ready=False), wave=2, turn=3),
                  observation("next-turn-3", allies=self._party(used_ready=False), wave=2, turn=3)]
        result = BattleController(FakeDriver(frames), sleeper=lambda _: None, max_polls=3).execute(
            Action("skill", servant=1, skill=1))
        self.assertEqual("uncertain", result.status)
