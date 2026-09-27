import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "FGO-py"))

from fgo_agent import Action, ActionResult, Observation
from fgo_agent.battle_runner import BattleRunner


def observation(identifier, phase="tactical", wave=1, turn=1):
    return Observation(identifier, phase, "now", wave=wave, turn=turn)


class Clock:
    def __init__(self): self.value = 0
    def __call__(self): return self.value
    def sleep(self, seconds): self.value += seconds


class Controller:
    def __init__(self, observations, results=()):
        self.observations, self.results, self.expected = iter(observations), iter(results), []
    def _observe(self): return next(self.observations)
    def execute(self, action, *, expected, time_budget=None):
        self.expected.append(expected.id)
        self.time_budgets = getattr(self, 'time_budgets', []) + [time_budget]
        return next(self.results)


class Policy:
    last_decision = {"policy": "test"}
    def __init__(self): self.results = []
    def next_action(self, observation): return Action("attack")
    def observe_result(self, action, result): self.results.append(result)


class Trace:
    def __init__(self): self.rows = []
    def record(self, event, payload): self.rows.append((event, payload))


class BattleRunnerTests(unittest.TestCase):
    def test_completes_only_after_two_terminal_observations(self):
        action = Action("attack")
        result = ActionResult(action, "confirmed", "one", "two")
        controller = Controller([observation("one"), observation("two", "result"), observation("three", "result")], [result])
        policy, trace, clock = Policy(), Trace(), Clock()
        actual = BattleRunner(controller, policy, trace, monotonic=clock, sleeper=clock.sleep, poll_interval=0).run()
        self.assertEqual("completed", actual["status"])
        self.assertEqual(("victory", "three"), (actual["outcome"], actual["terminal_observation_id"]))
        self.assertEqual(["one"], controller.expected)
        self.assertEqual(1, len(actual["actions"]))
        self.assertEqual(["decision", "battle_result"], [item[0] for item in trace.rows])

    def test_mixed_terminal_frames_do_not_claim_victory_and_two_defeats_classify_defeat(self):
        controller = Controller([observation("one", "result"), observation("two", "defeat"), observation("three", "defeat")])
        actual = BattleRunner(controller, Policy(), poll_interval=0).run()
        self.assertEqual("completed", actual["status"])
        self.assertEqual(("defeat", "three"), (actual["outcome"], actual["terminal_observation_id"]))
        self.assertEqual(0, actual["action_count"])

    def test_unknown_frames_are_bounded_without_actions(self):
        controller, policy, clock = Controller([observation("one", "unknown", None, None)] * 3), Policy(), Clock()
        actual = BattleRunner(controller, policy, monotonic=clock, sleeper=clock.sleep, unknown_polls=3, poll_interval=0,
                              unknown_seconds=0).run()
        self.assertEqual(("uncertain", 0), (actual["status"], actual["action_count"]))
        self.assertEqual([], controller.expected)

    def test_a_long_animation_is_not_mistaken_for_a_stuck_screen(self):
        # Live 2026-09-25: five unknown frames took ~3 s after the speed pass, shorter
        # than Kama's skill animation, and a healthy battle was stopped.
        clock = Clock()
        frames = [observation(f"u{i}", "unknown", None, None) for i in range(6)] + [observation("result", "result", None, None)] * 2
        controller = Controller(frames)
        runner = BattleRunner(controller, Policy(), monotonic=clock, sleeper=clock.sleep, unknown_polls=5, poll_interval=0.5)
        actual = runner.run()
        self.assertNotEqual("unknown observation limit reached without input", actual.get("reason"))

    def test_stops_after_nonconfirmed_action(self):
        action = Action("attack")
        controller = Controller([observation("one")], [ActionResult(action, "uncertain", "one", None, reason="no evidence")])
        # With continuations disabled, any unconfirmed action still ends the battle.
        actual = BattleRunner(controller, Policy(), poll_interval=0, unconfirmed_continuations=0).run()
        self.assertEqual("uncertain", actual["status"])
        self.assertEqual(1, actual["action_count"])

    def test_run_rejects_nonpositive_bounds_before_observation(self):
        runner = BattleRunner(Controller([]), Policy(), poll_interval=0)
        for name in ("max_actions", "max_turns", "battle_timeout"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    runner.run(**{name: 0})

    def test_model_failure_stops_without_controller_input(self):
        class FailedPolicy(Policy):
            def next_action(self, observation):
                raise ValueError('invalid choice')
        controller = Controller([observation('one'), observation('two')])
        result = BattleRunner(controller, FailedPolicy(), poll_interval=0).run()
        self.assertEqual('uncertain', result['status'])
        self.assertEqual([], controller.expected)

    def test_one_stalled_decision_is_retried_from_a_fresh_frame(self):
        class FlakyPolicy(Policy):
            calls = 0
            def next_action(self, observation):
                FlakyPolicy.calls += 1
                if FlakyPolicy.calls == 1:
                    raise RuntimeError('model call timed out')
                return Action("attack")
        controller = Controller([observation('stale'), observation('fresh'),
                                 observation('end', 'result'), observation('end-2', 'result')],
                                [ActionResult(Action("attack"), "confirmed", "fresh", "end")])
        result = BattleRunner(controller, FlakyPolicy(), poll_interval=0).run()
        self.assertEqual('completed', result['status'])
        self.assertEqual(['fresh'], controller.expected, "the retry must decide from the fresh frame")

    def test_retries_are_bounded_and_never_send_input(self):
        class FailedPolicy(Policy):
            def next_action(self, observation):
                raise RuntimeError('model call timed out')
        controller = Controller([observation(str(i)) for i in range(5)])
        result = BattleRunner(controller, FailedPolicy(), poll_interval=0, policy_retries=2).run()
        self.assertEqual('uncertain', result['status'])
        self.assertEqual([], controller.expected)

    def test_model_latency_cannot_send_after_battle_budget(self):
        clock = Clock()
        class SlowPolicy(Policy):
            def next_action(self, observation):
                clock.value += 3
                return Action('attack')
        controller = Controller([observation('one')])
        result = BattleRunner(controller, SlowPolicy(), monotonic=clock, poll_interval=0).run(battle_timeout=2)
        self.assertEqual('stopped', result['status'])
        self.assertEqual([], controller.expected)

    def test_runner_passes_only_remaining_battle_budget_to_controller(self):
        clock = Clock()
        action = Action("attack")
        controller = Controller([observation("one")], [ActionResult(action, "uncertain", "one", None)])
        result = BattleRunner(controller, Policy(), monotonic=clock, poll_interval=0,
                              unconfirmed_continuations=0).run(battle_timeout=2)
        self.assertEqual("uncertain", result["status"])
        self.assertEqual([2], controller.time_budgets)


if __name__ == "__main__":
    unittest.main()


class ReconnaissanceTests(unittest.TestCase):
    def test_reconnaissance_runs_once_then_decides_from_a_fresh_frame(self):
        calls = []
        def recon():
            calls.append(1)
            return {"status": "ok", "backline": []}
        controller = Controller([observation('first'), observation('fresh'),
                                 observation('end', 'result'), observation('end-2', 'result')],
                                [ActionResult(Action("attack"), "confirmed", "fresh", "end")])
        result = BattleRunner(controller, Policy(), poll_interval=0, reconnoitre=recon).run()
        self.assertEqual('completed', result['status'])
        self.assertEqual(1, len(calls))
        self.assertEqual(['fresh'], controller.expected)
        self.assertEqual('ok', result['order_change_reconnaissance']['status'])

    def test_failed_reconnaissance_does_not_end_the_battle(self):
        def recon():
            raise RuntimeError('palette not recognized')
        controller = Controller([observation('first'), observation('fresh'),
                                 observation('end', 'result'), observation('end-2', 'result')],
                                [ActionResult(Action("attack"), "confirmed", "fresh", "end")])
        result = BattleRunner(controller, Policy(), poll_interval=0, reconnoitre=recon).run()
        self.assertEqual('completed', result['status'])
        self.assertEqual('error', result['order_change_reconnaissance']['status'])


class UnconfirmedContinuationTests(unittest.TestCase):
    def test_an_unconfirmed_skill_continues_from_a_fresh_frame(self):
        class SkillThenAttack(Policy):
            calls = 0
            def next_action(self, observation):
                SkillThenAttack.calls += 1
                if SkillThenAttack.calls == 1:
                    return Action("skill", servant=2, skill=2, target=3)
                return Action("attack")
        controller = Controller([observation('one'), observation('fresh'),
                                 observation('end', 'result'), observation('end-2', 'result')],
                                [ActionResult(Action("skill"), "uncertain", "one", None, reason="timed out"),
                                 ActionResult(Action("attack"), "confirmed", "fresh", "end")])
        policy = SkillThenAttack()
        result = BattleRunner(controller, policy, poll_interval=0).run()
        self.assertEqual('completed', result['status'])
        self.assertEqual(['one', 'fresh'], controller.expected)
        self.assertEqual("uncertain", policy.results[0].status)

    def test_unconfirmed_cards_still_stop_the_battle(self):
        class Cards(Policy):
            def next_action(self, observation):
                return Action("cards", cards=(1, 2, 3))
        controller = Controller([observation('one', 'cards')],
                                [ActionResult(Action("cards"), "uncertain", "one", None)])
        self.assertEqual('uncertain', BattleRunner(controller, Cards(), poll_interval=0).run()['status'])

    def test_continuations_are_bounded(self):
        class Skill(Policy):
            def next_action(self, observation):
                return Action("skill", servant=1, skill=1, target=1)
        frames = [observation(str(i)) for i in range(10)]
        results = [ActionResult(Action("skill"), "uncertain", str(i), None) for i in range(10)]
        result = BattleRunner(Controller(frames, results), Skill(), poll_interval=0,
                              unconfirmed_continuations=2).run()
        self.assertEqual('uncertain', result['status'])
        self.assertEqual(3, result['action_count'])


class ScoutTests(unittest.TestCase):
    def test_a_scout_report_is_followed_by_a_decision_from_a_fresh_frame(self):
        reports = iter([{"status": "ok", "read": [1]}, None])
        seen = []

        def scout(observation):
            seen.append(observation.id)
            return next(reports)
        controller = Controller([observation('one'), observation('fresh'), observation('end', 'result'),
                                 observation('end-2', 'result')],
                                [ActionResult(Action("attack"), "confirmed", "fresh", "end")])
        trace = Trace()
        result = BattleRunner(controller, Policy(), trace, poll_interval=0, scout=scout).run()
        self.assertEqual('completed', result['status'])
        self.assertEqual(['one', 'fresh'], seen)
        self.assertEqual(['fresh'], controller.expected, "never decide from the frame before inspection")
        self.assertIn(("enemy_inspection", {"status": "ok", "read": [1]}), trace.rows)

    def test_an_open_dialog_is_handed_to_the_scout_to_close(self):
        closed = []
        controller = Controller([observation('dialog', 'enemy_profile'), observation('end', 'result'),
                                 observation('end-2', 'result')])
        scout = lambda o: closed.append(o.id) or {"status": "ok"}
        result = BattleRunner(controller, Policy(), poll_interval=0, scout=scout).run()
        self.assertEqual('completed', result['status'])
        self.assertEqual(['dialog'], closed)


class SkillErrorDismissTests(unittest.TestCase):
    def test_an_open_skill_error_dialog_is_closed_before_deciding(self):
        closed = []

        class Dismissing(Controller):
            def dismiss_skill_error(self, deadline):
                closed.append(deadline)
                return observation('back')
        controller = Dismissing([observation('err', 'skill_error'), observation('end', 'result'),
                                 observation('end-2', 'result')])
        result = BattleRunner(controller, Policy(), poll_interval=0).run()
        self.assertEqual('completed', result['status'])
        self.assertEqual(1, len(closed))


class CardsBackTests(unittest.TestCase):
    """Live 2026-09-27: a run resumed on the card screen had no trusted turn and
    stopped as unknown; Back returns to the tactical screen instead."""

    def test_a_card_screen_without_a_turn_goes_back_first(self):
        backs = []

        class Returning(Controller):
            def back_from_cards(self, deadline):
                backs.append(deadline)
                return observation('back')
        controller = Returning([observation('cards', 'cards', turn=None), observation('end', 'result'),
                                observation('end-2', 'result')])
        result = BattleRunner(controller, Policy(), poll_interval=0).run()
        self.assertEqual('completed', result['status'])
        self.assertEqual(1, len(backs))

    def test_a_card_screen_with_a_known_turn_is_played(self):
        backs = []

        class Returning(Controller):
            def back_from_cards(self, deadline):
                backs.append(deadline)
        controller = Returning([observation('cards', 'cards'), observation('end', 'result'),
                                observation('end-2', 'result')],
                               [ActionResult(Action("attack"), "confirmed", "cards", "end")])
        BattleRunner(controller, Policy(), poll_interval=0).run()
        self.assertEqual([], backs)


class RejectedCardsTests(unittest.TestCase):
    def test_cards_the_game_did_not_take_are_decided_again(self):
        class CardPolicy(Policy):
            def next_action(self, observation): return Action("cards", cards=(1, 2, 3))
        controller = Controller([observation('cards', 'cards'), observation('cards-2', 'cards'),
                                 observation('end', 'result'), observation('end-2', 'result')],
                                [ActionResult(Action("cards", cards=(1, 2, 3)), "rejected", "cards", "cards",
                                              reason="the game did not attack"),
                                 ActionResult(Action("cards", cards=(1, 2, 3)), "confirmed", "cards-2", "end")])
        result = BattleRunner(controller, CardPolicy(), poll_interval=0).run()
        self.assertEqual('completed', result['status'])
