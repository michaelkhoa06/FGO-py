"""Plan-ahead mode: one whole-battle schedule, executed without model calls while
the screen matches it, and re-planned when it does not."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "FGO-py"))

from fgo_agent import ActionResult, Observation
from fgo_agent.planner import PlannerPolicy

# Servant 10: skills 1 and 3 target itself; skill 2 is a special mode the planner never offers.
DATA = {10: (0, 0, 0, 0, (0, 0), ((1, 1), (1, 6), (1, 0)))}


def ally(slot=1, ready=(1, 3), np=0):
    return {"slot": slot, "id": 10, "hp": 100, "np": np,
            "skills": [{"slot": index, "ready": index in ready} for index in (1, 2, 3)]}


def tactical(name, turn, wave=1, ready=(1, 3), enemy_hp=5000):
    return Observation(name, "tactical", "now", allies=[ally(ready=ready)], wave=wave, turn=turn,
                       enemies=[{"slot": 1, "hp": enemy_hp}], signals={"stage_total": 3})


def cards(name, turn, wave=1, np_ready=True):
    faces = [{"slot": slot, "type": kind, "sealed": False}
             for slot, kind in ((1, "arts"), (2, "quick"), (3, "buster"), (4, "arts"), (5, "buster"))]
    faces.append({"slot": 6, "type": "np", "sealed": False, "ready": np_ready})
    return Observation(name, "cards", "now", cards=faces, wave=wave, turn=turn)


class Backend:
    """Answers battle-plan requests from a script and records every call."""

    def __init__(self, *plans):
        self.plans, self.requests = list(plans), []
        self.last_call = {"model": "sol"}

    def choose(self, request, schema):
        self.requests.append(request)
        if request.get("task") != "battle_plan":
            return {"observation_id": request["observation_id"], "request_id": request["request_id"],
                    "action_id": "attack" if request["phase"] == "tactical" else
                    request["allowed_actions"][0]["id"], "reason": "fallback", "uncertainty": []}
        return {"observation_id": request["observation_id"], "request_id": request["request_id"],
                "turns": self.plans.pop(0), "reason": "schedule", "uncertainty": []}


def confirm(policy, action):
    policy.observe_result(action, ActionResult(action, "confirmed", "a", "b", skill_consumed=True))


PLAN = [{"turn": 1, "wave": 1, "skills": ["s1-1-t1", "s1-3-t1"], "np": [1]},
        {"turn": 2, "wave": 2, "skills": [], "np": [1]},
        {"turn": 3, "wave": 3, "skills": ["s1-1-t1"], "np": [1]}]


class PlanAheadTests(unittest.TestCase):
    def test_one_call_plans_the_battle_and_the_turn_runs_without_more_calls(self):
        backend = Backend(PLAN)
        policy = PlannerPolicy(DATA, backend, plan_ahead=True)
        first = policy.next_action(tactical("t1", 1))
        self.assertEqual(("skill", 1, 1), (first.kind, first.servant, first.skill))
        confirm(policy, first)
        second = policy.next_action(tactical("t1b", 1, ready=(3,)))
        self.assertEqual(("skill", 1, 3), (second.kind, second.servant, second.skill))
        confirm(policy, second)
        self.assertEqual("attack", policy.next_action(tactical("t1c", 1, ready=())).kind)
        chosen = policy.next_action(cards("c1", 1))
        self.assertIn(6, chosen.cards, "the planned NP must be in the chain")
        self.assertEqual(1, len(backend.requests), "everything after the plan is served without the model")
        self.assertEqual("battle_plan", backend.requests[0]["task"])
        self.assertEqual("battle_plan", policy.last_decision["source"])

    def test_the_next_turn_follows_the_schedule_too(self):
        backend = Backend(PLAN)
        policy = PlannerPolicy(DATA, backend, plan_ahead=True)
        for action_ready in ((1, 3), (3,)):
            confirm(policy, policy.next_action(tactical("t", 1, ready=action_ready)))
        policy.next_action(tactical("t1c", 1, ready=()))
        policy.next_action(cards("c1", 1))
        self.assertEqual("attack", policy.next_action(tactical("t2", 2, wave=2, ready=())).kind)
        self.assertEqual(1, len(backend.requests))

    def test_a_wave_that_did_not_fall_triggers_one_replan_with_the_deviation(self):
        backend = Backend(PLAN, [{"turn": 2, "wave": 1, "skills": [], "np": [1]}])
        policy = PlannerPolicy(DATA, backend, plan_ahead=True)
        for action_ready in ((1, 3), (3,)):
            confirm(policy, policy.next_action(tactical("t", 1, ready=action_ready)))
        policy.next_action(tactical("t1c", 1, ready=()))
        policy.next_action(cards("c1", 1))
        self.assertEqual("attack", policy.next_action(tactical("t2", 2, wave=1, ready=())).kind)
        self.assertEqual(2, len(backend.requests))
        replan = backend.requests[1]["planning"]
        self.assertIn("expected wave 2", replan["deviation"])
        self.assertEqual(3, len(replan["previous_plan"]))

    def test_a_planned_skill_that_is_not_ready_replans(self):
        backend = Backend([{"turn": 1, "wave": 1, "skills": ["s1-1-t1"], "np": []},
                           {"turn": 2, "wave": 1, "skills": ["s1-1-t1"], "np": [1]}],
                          [{"turn": 2, "wave": 1, "skills": [], "np": [1]}])
        policy = PlannerPolicy(DATA, backend, plan_ahead=True)
        confirm(policy, policy.next_action(tactical("t1", 1)))
        policy.next_action(tactical("t1b", 1, ready=(3,)))
        policy.next_action(cards("c1", 1, np_ready=False))
        self.assertEqual("attack", policy.next_action(tactical("t2", 2, ready=(3,))).kind)
        self.assertIn("s1-1-t1 on turn 2 is not available", backend.requests[1]["planning"]["deviation"])

    def test_an_enemy_hp_refill_within_the_wave_replans(self):
        backend = Backend([{"turn": 1, "wave": 1, "skills": [], "np": []},
                           {"turn": 2, "wave": 1, "skills": [], "np": [1]}],
                          [{"turn": 2, "wave": 1, "skills": [], "np": [1]}])
        policy = PlannerPolicy(DATA, backend, plan_ahead=True)
        policy.next_action(tactical("t1", 1, ready=(), enemy_hp=5000))
        policy.next_action(cards("c1", 1))
        policy.next_action(tactical("t2", 2, ready=(), enemy_hp=90000))
        self.assertEqual(2, len(backend.requests))
        self.assertIn("rose from 5000 to 90000", backend.requests[1]["planning"]["deviation"])

    def test_an_unusable_plan_falls_back_to_turn_by_turn_decisions(self):
        backend = Backend([{"turn": 1, "wave": 1, "skills": ["s1-2-t1"], "np": []}])
        policy = PlannerPolicy(DATA, backend, plan_ahead=True)
        self.assertEqual("attack", policy.next_action(tactical("t1", 1)).kind)
        self.assertEqual(["battle_plan", None], [r.get("task") for r in backend.requests])

    def test_plans_must_start_now_and_run_consecutively(self):
        for bad in ([{"turn": 2, "wave": 1, "skills": [], "np": []}],
                    [{"turn": 1, "wave": 1, "skills": [], "np": []}, {"turn": 3, "wave": 2, "skills": [], "np": []}],
                    [{"turn": 1, "wave": 2, "skills": [], "np": []}]):
            backend = Backend(bad)
            policy = PlannerPolicy(DATA, backend, plan_ahead=True)
            policy.next_action(tactical("t1", 1))
            self.assertIsNone(policy._battle_plan, bad)

    def test_catalog_cooldowns_reach_the_request(self):
        catalog = {10: {"name": "Test", "skills": [{"slot": 1, "variants": [
            {"id": 1, "name": "A", "description": "d", "cooldown_by_level": [7, 7, 7, 7, 7, 6, 6, 6, 6, 5]}]}]}}
        request = PlannerPolicy(DATA, Backend(PLAN), strategy_catalog=catalog).build_request(tactical("t1", 1))
        skill = request["servant_metadata"][0]["catalog_skills"][0]["variants"][0]
        self.assertEqual(5, skill["cooldown_by_level"][-1])

    def test_plan_ahead_is_off_by_default(self):
        backend = Backend(PLAN)
        PlannerPolicy(DATA, backend).next_action(tactical("t1", 1))
        self.assertIsNone(backend.requests[0].get("task"))


if __name__ == "__main__":
    unittest.main()


class RosterTests(unittest.TestCase):
    """Live 2026-09-23: the front line died, the back line took the slots, and a
    planned "slot 1 skill 3" cast the newcomer's skill instead of the planned one."""

    def test_a_different_servant_in_a_planned_slot_replans_before_any_step(self):
        backend = Backend([{"turn": 1, "wave": 1, "skills": [], "np": []},
                           {"turn": 2, "wave": 1, "skills": ["s1-1-t1"], "np": [1]}],
                          [{"turn": 2, "wave": 1, "skills": [], "np": [1]}])
        data = {**DATA, 20: DATA[10]}
        policy = PlannerPolicy(data, backend, plan_ahead=True)
        policy.next_action(tactical("t1", 1, ready=()))
        policy.next_action(cards("c1", 1))
        replaced = tactical("t2", 2)
        replaced.allies[0]["id"] = 20
        self.assertEqual("attack", policy.next_action(replaced).kind, "the old step must not run on the newcomer")
        self.assertIn("now holds servant 20", backend.requests[1]["planning"]["deviation"])

    def test_a_planned_order_change_is_not_a_deviation(self):
        policy = PlannerPolicy(DATA, Backend(PLAN), plan_ahead=True)
        policy._battle_plan = {"turns": {}, "roster": {1: 10}}
        observation = tactical("t", 2)
        observation.signals["backline"] = [{"slot": 1, "name": "Oberon", "servant_id": 316}]
        from fgo_agent import Action
        policy._expect_order_change(observation, Action("order_change", servant=1, target=1))
        swapped = tactical("t2", 2)
        swapped.allies[0]["id"] = 316
        self.assertIsNone(policy._roster_change(swapped))


class PlanQualityTests(unittest.TestCase):
    def test_a_wave_without_an_np_is_sent_back_once(self):
        lazy = [{"turn": 1, "wave": 1, "skills": [], "np": [1]}, {"turn": 2, "wave": 2, "skills": [], "np": []}]
        fixed = [{"turn": 1, "wave": 1, "skills": [], "np": [1]}, {"turn": 2, "wave": 2, "skills": [], "np": [1]}]
        backend = Backend(lazy, fixed)
        policy = PlannerPolicy(DATA, backend, plan_ahead=True)
        policy.next_action(tactical("t1", 1, ready=()))
        self.assertEqual(2, len(backend.requests))
        self.assertIn("wave(s) [2]", backend.requests[1]["planning"]["feedback"])
        self.assertEqual([1], policy._battle_plan["turns"][2]["np"])

    def test_the_second_answer_stands(self):
        lazy = [{"turn": 1, "wave": 1, "skills": [], "np": []}]
        backend = Backend(lazy, lazy)
        policy = PlannerPolicy(DATA, backend, plan_ahead=True)
        policy.next_action(tactical("t1", 1, ready=()))
        self.assertEqual(2, len(backend.requests))
        self.assertIsNotNone(policy._battle_plan)


class PlanEffortTests(unittest.TestCase):
    def test_plans_use_the_higher_effort_and_turns_the_normal_one(self):
        import tempfile
        from fgo_agent.codex_backend import CodexBackend
        with tempfile.TemporaryDirectory() as directory:
            backend = CodexBackend(Path(directory), timeout=3)
            args = ("codex.exe", Path(directory), Path(directory) / "s.json", Path(directory) / "o.json")
            self.assertIn('model_reasoning_effort="medium"', backend._command(*args, backend.plan_effort))
            self.assertIn('model_reasoning_effort="low"', backend._command(*args))


class SealedHandTests(unittest.TestCase):
    """Live 2026-09-23: every card sealed; the game still lets sealed cards be picked."""

    def hand(self, sealed_slots, np_ready=False):
        cards = [{"slot": s, "type": t, "sealed": s in sealed_slots}
                 for s, t in ((1, "quick"), (2, "quick"), (3, "quick"), (4, "arts"), (5, "quick"))]
        return cards + [{"slot": 6, "type": "np", "sealed": None, "ready": np_ready}]

    def test_a_fully_sealed_hand_offers_sealed_cards_to_pass_the_turn(self):
        from fgo_agent.card_rules import selectable_slots
        self.assertEqual([1, 2, 3, 4, 5], selectable_slots(self.hand({1, 2, 3, 4, 5})))
        policy = PlannerPolicy(DATA, Backend(PLAN))
        allowed = policy._allowed_actions(Observation("c", "cards", "now", cards=self.hand({1, 2, 3, 4, 5})))
        self.assertTrue(allowed)

    def test_stunned_cards_fill_the_chain_last(self):
        """Live 2026-09-27: two servants stunned left one face card and one NP."""
        cards = [{"slot": 1, "type": "arts", "sealed": False}]
        cards += [{"slot": s, "type": "buster", "sealed": True, "stunned": True} for s in (2, 3, 4, 5)]
        cards += [{"slot": 6, "type": "np", "sealed": False, "ready": True}]
        policy = PlannerPolicy(DATA, Backend(PLAN))
        allowed = policy._allowed_actions(Observation("c", "cards", "now", cards=cards))
        chains = {a.cards for a in allowed.values() if a.kind == "cards"}
        self.assertTrue(chains)
        self.assertTrue(all(set(chain[:2]) == {1, 6} and chain[2] in (2, 3, 4, 5) for chain in chains))

    def test_sealed_cards_only_fill_a_short_hand(self):
        from fgo_agent.card_rules import selectable_slots
        self.assertEqual([1, 2, 3], selectable_slots(self.hand({4, 5})))
        self.assertEqual([1, 2, 3, 4, 5], selectable_slots(self.hand({3, 4, 5})))

    def test_the_controller_accepts_sealed_fill_and_rejects_it_otherwise(self):
        from fgo_agent import Action, BattleController
        control = BattleController(object(), sleeper=lambda _: None)
        sealed = Observation("c", "cards", "now", cards=self.hand({1, 2, 3, 4, 5}))
        self.assertIsNone(control._validate_cards(Action("cards", cards=(1, 2, 3)), sealed))
        mostly_free = Observation("c", "cards", "now", cards=self.hand({5}))
        self.assertIsNotNone(control._validate_cards(Action("cards", cards=(1, 2, 5)), mostly_free))

    def test_an_np_under_np_seal_is_never_offered_even_if_the_card_looks_ready(self):
        from fgo_agent.card_rules import selectable_slots
        hand = self.hand({1, 2, 3, 4, 5}, np_ready=True)
        hand[-1]["sealed"] = False  # seen live: one frame read the sealed NP as ready
        signals = {"ally_statuses": [{"slot": 1, "read_at": {"turn": 1},
                                      "effects": [{"title": "NP Seal", "duration": "2 Turn(s)"}]}]}
        self.assertNotIn(6, selectable_slots(hand, signals, 1))
        self.assertNotIn(6, selectable_slots(hand, signals, 2))
        self.assertIn(6, selectable_slots(hand, signals, 3), "the seal has expired by turn 3")
        unreadable = {"ally_statuses": [{"slot": 1, "read_at": {"turn": 1}, "effects": [{"title": "NP Seal"}]}]}
        self.assertNotIn(6, selectable_slots(hand, unreadable, 9))


class DefensiveReserveTests(unittest.TestCase):
    """Live 2026-09-24 (Black Wolf): the plan spent the only invincibility on turn 1
    and nothing defensive was left when the boss reached full charge."""

    CATALOG = {10: {"name": "Guardian", "skills": [
        {"slot": 1, "variants": [{"description": "Apply Invincible (1 turn) for one ally"}]},
        {"slot": 3, "variants": [{"description": "Increase ATK (3 turns) for yourself"}]}],
        "noble_phantasms": [{"description": "Deal heavy damage to one enemy"}]}}

    def battle(self, charge=(0, 3)):
        return Observation("t1", "tactical", "now", allies=[ally(ready=(1, 3))], wave=1, turn=1,
                           enemies=[{"slot": 1, "hp": 200000, "charge": list(charge)}], signals={"stage_total": 1})

    def plan(self, turn1, turn4=()):
        return {1: {"wave": 1, "skills": list(turn1), "np": [1]}, 2: {"wave": 1, "skills": [], "np": []},
                3: {"wave": 1, "skills": [], "np": [1]}, 4: {"wave": 1, "skills": list(turn4), "np": []}}

    def policy(self, backend=None, objective="survive_and_win"):
        return PlannerPolicy(DATA, backend or Backend(PLAN), strategy_catalog=self.CATALOG,
                             plan_ahead=True, objective=objective)

    def test_spending_every_defence_before_the_enemy_np_is_flagged(self):
        issue = self.policy()._defense_issue(self.plan(["s1-1-t1", "s1-3-t1"]), self.battle())
        self.assertIn("fires its Noble Phantasm on turn 4", issue)
        self.assertIn("s1-1", issue)

    def test_keeping_or_using_the_defence_on_the_danger_turn_passes(self):
        policy = self.policy()
        self.assertIsNone(policy._defense_issue(self.plan(["s1-3-t1"]), self.battle()))
        self.assertIsNone(policy._defense_issue(self.plan(["s1-3-t1"], ["s1-1-t1"]), self.battle()))

    def test_no_known_charge_or_no_defence_means_no_check(self):
        policy = self.policy()
        self.assertIsNone(policy._defense_issue(self.plan(["s1-1-t1"]), self.battle(charge=(0, 0))))
        plain = PlannerPolicy(DATA, Backend(PLAN), plan_ahead=True)
        self.assertIsNone(plain._defense_issue(self.plan(["s1-1-t1"]), self.battle()))

    def test_a_reckless_plan_is_sent_back_once_with_the_reason(self):
        rows = lambda first, last: [{"turn": t, "wave": 1, "skills": s, "np": n} for t, s, n in
                                    ((1, first, [1]), (2, [], []), (3, [], [1]), (4, last, []))]
        backend = Backend(rows(["s1-1-t1"], []), rows([], ["s1-1-t1"]))
        policy = self.policy(backend)
        policy.next_action(self.battle())
        self.assertEqual(2, len(backend.requests))
        self.assertIn("turn 4", backend.requests[1]["planning"]["feedback"])
        self.assertEqual(["s1-1-t1"], policy._battle_plan["turns"][4]["skills"])

    def test_farming_plans_are_not_checked(self):
        rows = [{"turn": t, "wave": 1, "skills": s, "np": n} for t, s, n in
                ((1, ["s1-1-t1"], [1]), (2, [], []), (3, [], [1]), (4, [], []))]
        backend = Backend(rows)
        self.policy(backend, objective="farm_fast").next_action(self.battle())
        self.assertEqual(1, len(backend.requests))

    def test_a_danger_turn_after_the_plan_ends_still_counts(self):
        short = {1: {"wave": 1, "skills": ["s1-1-t1"], "np": [1]}, 2: {"wave": 1, "skills": [], "np": [1]}}
        self.assertIn("turn 4", self.policy()._defense_issue(short, self.battle()))
        kept = {1: {"wave": 1, "skills": ["s1-3-t1"], "np": [1]}, 2: {"wave": 1, "skills": [], "np": [1]}}
        self.assertIsNone(self.policy()._defense_issue(kept, self.battle()))


class DurableEnemyTests(unittest.TestCase):
    """Live 2026-09-24 (Black Wolf): the plan ended on turn 4 expecting two NPs to kill a
    regenerating, damage-cutting boss whose own NP came on turn 6; it out-lasted the party."""

    REGEN = [{"slot": 1, "effects": [{"title": "Loyalty to the Queen",
                                      "text": "Increase Critical Hit Rate & restore HP at the end of the turn"},
                                     {"title": "Damage Cut", "text": "Decrease damage taken"}]}]

    def battle(self, profiles=REGEN):
        return Observation("t1", "tactical", "now", allies=[ally()], wave=1, turn=1,
                           enemies=[{"slot": 1, "hp": 248883, "charge": [0, 5]}],
                           signals={"stage_total": 1, "enemy_profiles": profiles})

    def turns(self, last):
        return {t: {"wave": 1, "skills": [], "np": [1] if t in (1, last) else []} for t in range(1, last + 1)}

    def test_a_plan_ending_before_a_durable_bosss_np_is_flagged(self):
        issue = PlannerPolicy(DATA, Backend(PLAN), plan_ahead=True)._durability_issue(self.turns(4), self.battle())
        self.assertIn("ends on turn 4", issue)
        self.assertIn("turn 6", issue)

    def test_a_plan_covering_the_np_turn_or_a_plain_enemy_passes(self):
        policy = PlannerPolicy(DATA, Backend(PLAN), plan_ahead=True)
        self.assertIsNone(policy._durability_issue(self.turns(6), self.battle()))
        plain = [{"slot": 1, "effects": [{"title": "Faerie Witch", "text": "Inflict Curse during normal attacks"}]}]
        self.assertIsNone(policy._durability_issue(self.turns(4), self.battle(plain)))
        self.assertIsNone(policy._durability_issue(self.turns(4), self.battle([])))

    def test_the_revision_asks_for_a_longer_plan(self):
        rows = lambda last: [{"turn": t, "wave": 1, "skills": [], "np": [1] if t in (1, last) else []}
                             for t in range(1, last + 1)]
        backend = Backend(rows(4), rows(6))
        policy = PlannerPolicy(DATA, backend, plan_ahead=True)
        policy.next_action(self.battle())
        self.assertEqual(2, len(backend.requests))
        self.assertIn("Extend the schedule through turn 6", backend.requests[1]["planning"]["feedback"])
        self.assertEqual(6, max(policy._battle_plan["turns"]))
        self.assertIn("Bosses outlast their HP bar", backend.requests[0]["instructions"])


class VitalsAndHeldNpTests(unittest.TestCase):
    """Live 2026-09-24 (Black Wolf rematch): Mash died on turn 3/4 while the plan still
    reserved her kit for turn 6, and 100%+ NPs sat unused while normal attacks won."""

    def fighter(self, hp_fraction=1.0, np=0, alive=True, ident=10):
        return {"slot": 1, "id": ident, "hp": 100 if alive else None, "hp_fraction": hp_fraction,
                "alive": alive, "np": np, "skills": [{"slot": i, "ready": False} for i in (1, 2, 3)]}

    def frame(self, turn, **kw):
        return Observation(f"t{turn}", "tactical", "now", allies=[self.fighter(**kw)], wave=1, turn=turn,
                           enemies=[{"slot": 1, "hp": 200000, "charge": [0, 5]}], signals={"stage_total": 1})

    def rows(self, first, last=4, np_turns=(1, 3)):
        return [{"turn": t, "wave": 1, "skills": [], "np": [1] if t in np_turns else []} for t in range(first, last + 1)]

    def test_an_ally_death_since_the_plan_replans(self):
        backend = Backend(self.rows(1), self.rows(2))
        policy = PlannerPolicy(DATA, backend, plan_ahead=True)
        policy.next_action(self.frame(1))
        policy.next_action(self.frame(2, hp_fraction=0, alive=False, ident=None))
        self.assertEqual(2, len(backend.requests))
        self.assertIn("died since the plan", backend.requests[1]["planning"]["deviation"])

    def test_heavy_hp_loss_since_the_plan_replans_but_a_scratch_does_not(self):
        backend = Backend(self.rows(1), self.rows(2))
        policy = PlannerPolicy(DATA, backend, plan_ahead=True)
        policy.next_action(self.frame(1))
        policy.next_action(self.frame(1, hp_fraction=0.8))
        self.assertEqual(1, len(backend.requests))
        policy.next_action(self.frame(2, hp_fraction=0.3))
        self.assertIn("fell from 100% to 30%", backend.requests[1]["planning"]["deviation"])

    OFFENSIVE = {10: {"noble_phantasms": [{"description": "Deal heavy damage to one enemy"}]}}

    def test_a_charged_np_left_idle_is_sent_back(self):
        policy = PlannerPolicy(DATA, Backend(PLAN), plan_ahead=True, strategy_catalog=self.OFFENSIVE)
        turns = {t: {"wave": 1, "skills": [], "np": [1] if t == 4 else []} for t in range(1, 5)}
        self.assertIn("full OFFENSIVE NP gauge", policy._held_np_issue(turns, self.frame(1, np=120)))
        fired = {t: {"wave": 1, "skills": [], "np": [1] if t == 2 else []} for t in range(1, 5)}
        self.assertIsNone(policy._held_np_issue(fired, self.frame(1, np=120)))
        self.assertIsNone(policy._held_np_issue(turns, self.frame(1, np=60)))

    def test_a_np_kept_for_the_enemy_np_turn_or_sealed_is_not_flagged(self):
        policy = PlannerPolicy(DATA, Backend(PLAN), plan_ahead=True, strategy_catalog=self.OFFENSIVE)
        for_danger = {t: {"wave": 1, "skills": [], "np": [1] if t == 6 else []} for t in range(1, 7)}
        self.assertIsNone(policy._held_np_issue(for_danger, self.frame(1, np=120)))
        sealed = self.frame(1, np=120)
        sealed.signals["ally_statuses"] = [{"slot": 1, "read_at": {"turn": 1},
                                            "effects": [{"title": "NP Seal", "duration": "3 Turn(s)"}]}]
        idle = {t: {"wave": 1, "skills": [], "np": []} for t in range(1, 5)}
        self.assertIsNone(policy._held_np_issue(idle, sealed))


class NpRoleTests(unittest.TestCase):
    """User rule 2026-09-24: offensive NPs fire at the best moment soon, else as soon
    as possible; support NPs (stun, invincibility, damage buffs) are saved."""

    SUPPORT = {"noble_phantasms": [{"description": "Unknown"},
                                   {"description": "Increase DEF (3 turns) & apply Incoming Damage Cut for all allies"}]}
    OFFENSIVE = {"noble_phantasms": [{"description": "Increase ATK (1 turn) for yourself + Deal heavy damage to all enemies"}]}

    def test_roles_come_from_whether_an_np_deals_damage(self):
        self.assertEqual("support", PlannerPolicy._np_role(self.SUPPORT))
        self.assertEqual("offensive", PlannerPolicy._np_role(self.OFFENSIVE))
        self.assertEqual("support", PlannerPolicy._np_role(
            {"noble_phantasms": [{"description": "Decrease Charge & inflict Stun (1 turn) to all enemies"}]}))
        self.assertIsNone(PlannerPolicy._np_role({}))

    def test_a_full_support_np_may_wait_but_an_offensive_one_may_not(self):
        idle = {t: {"wave": 1, "skills": [], "np": []} for t in range(1, 5)}
        frame = Observation("t1", "tactical", "now", wave=1, turn=1, enemies=[{"slot": 1, "hp": 200000, "charge": [0, 5]}],
                            allies=[dict(ally(), np=150)])
        support = PlannerPolicy(DATA, Backend(PLAN), plan_ahead=True, strategy_catalog={10: self.SUPPORT})
        self.assertIsNone(support._held_np_issue(idle, frame))
        offensive = PlannerPolicy(DATA, Backend(PLAN), plan_ahead=True, strategy_catalog={10: self.OFFENSIVE})
        self.assertIn("OFFENSIVE", offensive._held_np_issue(idle, frame))

    def test_the_role_reaches_servant_metadata(self):
        request = PlannerPolicy(DATA, Backend(PLAN), strategy_catalog={10: self.SUPPORT}).build_request(tactical("t1", 1))
        self.assertEqual("support", request["servant_metadata"][0]["np_role"])


class PlanningEffortTests(unittest.TestCase):
    """Medium reasoning only for opening plans and crisis re-plans (2026-09-24)."""

    def test_opening_is_medium_and_a_minor_slip_is_low(self):
        backend = Backend([{"turn": 1, "wave": 1, "skills": ["s1-1-t1"], "np": []},
                           {"turn": 2, "wave": 1, "skills": ["s1-1-t1"], "np": [1]}],
                          [{"turn": 2, "wave": 1, "skills": [], "np": [1]}])
        policy = PlannerPolicy(DATA, backend, plan_ahead=True)
        confirm(policy, policy.next_action(tactical("t1", 1)))
        policy.next_action(tactical("t1b", 1, ready=(3,)))
        policy.next_action(cards("c1", 1, np_ready=False))
        policy.next_action(tactical("t2", 2, ready=(3,)))
        self.assertEqual(["medium", "low"], [r["planning"]["reasoning"] for r in backend.requests])

    def test_a_crisis_replan_stays_medium_and_a_revision_is_low(self):
        backend = Backend([{"turn": 1, "wave": 1, "skills": [], "np": [1]}, {"turn": 2, "wave": 1, "skills": [], "np": []}],
                          [{"turn": 2, "wave": 1, "skills": [], "np": [1]}])
        policy = PlannerPolicy(DATA, backend, plan_ahead=True)
        policy.next_action(tactical("t1", 1, ready=(), enemy_hp=5000))
        policy.next_action(cards("c1", 1))
        policy.next_action(tactical("t2", 2, ready=(), enemy_hp=90000))
        self.assertEqual("medium", backend.requests[1]["planning"]["reasoning"])
        lazy = [{"turn": 1, "wave": 1, "skills": [], "np": []}]
        backend = Backend(lazy, [{"turn": 1, "wave": 1, "skills": [], "np": [1]}])
        PlannerPolicy(DATA, backend, plan_ahead=True).next_action(tactical("t1", 1, ready=()))
        self.assertEqual(["medium", "low"], [r["planning"]["reasoning"] for r in backend.requests])

    def test_the_backend_honours_the_requested_effort(self):
        import tempfile
        from fgo_agent.codex_backend import CodexBackend
        with tempfile.TemporaryDirectory() as directory:
            backend = CodexBackend(Path(directory), timeout=3)
            seen = []
            backend._command = lambda *args: seen.append(args[-1]) or ["x"]
            import fgo_agent.codex_backend as module
            original = module.find_codex
            module.find_codex = lambda _: "codex.exe"
            try:
                for reasoning in ("low", "medium", None):
                    request = {"task": "battle_plan", "observation_id": "o", "request_id": "r",
                               "planning": {"reasoning": reasoning}}
                    try:
                        backend.choose(request, {"type": "object"})
                    except Exception:
                        pass
            finally:
                module.find_codex = original
            self.assertEqual(["low", "medium", "medium"], seen)


class ChargeJumpTests(unittest.TestCase):
    """Demeter adds 3 charge with one skill: the enemy NP comes turns early."""

    def test_a_charge_jump_triggers_a_crisis_replan(self):
        frame = lambda turn, charge: Observation(f"t{turn}", "tactical", "now", allies=[ally(ready=())], wave=1, turn=turn,
                                                 enemies=[{"slot": 1, "hp": 200000, "charge": [charge, 5]}],
                                                 signals={"stage_total": 1})
        rows = lambda first: [{"turn": t, "wave": 1, "skills": [], "np": [1]} for t in range(first, 9)]
        backend = Backend(rows(1), rows(2))
        policy = PlannerPolicy(DATA, backend, plan_ahead=True)
        policy.next_action(frame(1, 0))
        policy.next_action(cards("c1", 1))
        policy.next_action(frame(2, 3))
        self.assertEqual(2, len(backend.requests))
        self.assertIn("charge jumped from 0 to 3", backend.requests[1]["planning"]["deviation"])
        self.assertEqual("medium", backend.requests[1]["planning"]["reasoning"])

    def test_the_observed_charge_rate_sets_the_danger_turn(self):
        # Live 2026-09-25 (Morgan): +2-3 charge a turn; "+1 a turn" timed defence late.
        frame = lambda turn, charge: Observation(f"t{turn}", "tactical", "now", allies=[ally(ready=())], wave=1, turn=turn,
                                                 enemies=[{"slot": 1, "hp": 200000, "charge": [charge, 5]}],
                                                 signals={"stage_total": 1})
        rows = lambda first: [{"turn": t, "wave": 1, "skills": [], "np": [1]} for t in range(first, 9)]
        backend = Backend(rows(1), rows(2), rows(2))
        policy = PlannerPolicy(DATA, backend, plan_ahead=True)
        policy.next_action(frame(1, 0))
        policy.next_action(cards("c1", 1))
        second = frame(2, 3)
        policy.next_action(second)
        self.assertEqual({1: 3}, backend.requests[1]["planning"]["enemy_charge_gain_per_turn"])
        self.assertEqual([(3, 1)], PlannerPolicy._danger_turns(second), "3/5 at +3 a turn is full next turn")
        self.assertEqual([(4, 1)], PlannerPolicy._danger_turns(frame(2, 3)), "without history, +1 a turn")


class IncapacitatedTests(unittest.TestCase):
    """Avalon le Fae survey (Transhuman Duke: Terror stuns, Charm): a servant that
    cannot act has no usable cards, whether or not the card shows an overlay."""

    def test_a_charmed_servants_cards_and_np_are_withheld_until_it_expires(self):
        from fgo_agent.card_rules import selectable_slots
        hand = [{"slot": s, "type": "arts", "sealed": False, "servant": 1 if s <= 2 else 2} for s in range(1, 6)]
        hand.append({"slot": 6, "type": "np", "sealed": False, "ready": True, "servant": 1})
        signals = {"ally_statuses": [{"slot": 1, "read_at": {"turn": 3},
                                      "effects": [{"title": "Charm", "duration": "1 Turn(s)"}]}]}
        self.assertEqual([3, 4, 5], selectable_slots(hand, signals, 3))
        self.assertEqual([1, 2, 3, 4, 5, 6], selectable_slots(hand, signals, 4))

    def test_a_buff_that_mentions_nothing_similar_blocks_nothing(self):
        from fgo_agent.card_rules import selectable_slots
        hand = [{"slot": s, "type": "arts", "sealed": False, "servant": 1} for s in range(1, 6)]
        signals = {"ally_statuses": [{"slot": 1, "read_at": {"turn": 1},
                                      "effects": [{"title": "Stun Resist Up", "duration": "3 Turn(s)"}]}]}
        self.assertEqual([1, 2, 3, 4, 5], selectable_slots(hand, signals, 1))

    def test_immunities_and_resist_buffs_are_not_incapacitation(self):
        from fgo_agent.card_rules import incapacitated_allies
        for title in ("Charm Immunity", "Sleep Resist Up", "Stun Success Rate Up"):
            signals = {"ally_statuses": [{"slot": 1, "read_at": {"turn": 1},
                                          "effects": [{"title": title, "duration": "3 Turn(s)"}]}]}
            self.assertEqual(set(), incapacitated_allies(signals, 1), title)
        for title in ("Stun", "Charm", "Sleep", "Petrify", "Pigify"):
            signals = {"ally_statuses": [{"slot": 2, "read_at": {"turn": 1},
                                          "effects": [{"title": title, "duration": "1 Turn(s)"}]}]}
            self.assertEqual({2}, incapacitated_allies(signals, 1), title)


class GuideScopeTests(unittest.TestCase):
    """Guide consolidation 2026-09-24: each objective receives only its sections."""

    def test_story_calls_skip_farming_sections_and_farming_calls_skip_story(self):
        story = PlannerPolicy(DATA, Backend(PLAN), objective="survive_and_win").build_request(tactical("t1", 1))
        farm = PlannerPolicy(DATA, Backend(PLAN), objective="farm_fast").build_request(tactical("t1", 1))
        self.assertIn("## Story battles", story["instructions"])
        self.assertNotIn("## The standard three-turn procedure", story["instructions"])
        self.assertNotIn("## Farming", story["instructions"])
        self.assertIn("## The standard three-turn procedure", farm["instructions"])
        self.assertNotIn("## Story battles", farm["instructions"])
        for shared in ("## Turn plans", "## Card vocabulary", "## Evidence and limits"):
            self.assertIn(shared, story["instructions"])
            self.assertIn(shared, farm["instructions"])
        self.assertNotEqual(story["strategy_guide_sha256"], farm["strategy_guide_sha256"])

    def test_story_priorities_are_an_explicit_order(self):
        story = PlannerPolicy(DATA, Backend(PLAN), objective="survive_and_win").build_request(tactical("t1", 1))
        text = story["instructions"]
        order = [text.index(marker) for marker in ("1. **No ally dies", "2. **Fire offensive NPs",
                                                   "3. **Save support NPs", "4. **Otherwise, damage")]
        self.assertEqual(sorted(order), order)


class OneRevisionTests(unittest.TestCase):
    def test_several_objecting_checks_still_cost_one_revision(self):
        # No NP at all (idle wave) AND ends before a durable boss's NP: two objections.
        lazy = [{"turn": 1, "wave": 1, "skills": [], "np": []}]
        frame = Observation("t1", "tactical", "now", allies=[ally(ready=())], wave=1, turn=1,
                            enemies=[{"slot": 1, "hp": 300000, "charge": [0, 5]}],
                            signals={"stage_total": 1, "enemy_profiles": [{"slot": 1, "effects": [
                                {"title": "Regen", "text": "restore HP at the end of the turn"}]}]})
        backend = Backend(lazy, lazy)
        policy = PlannerPolicy(DATA, backend, plan_ahead=True)
        policy.next_action(frame)
        self.assertEqual(2, len(backend.requests), "one answer plus one revision, never more")
        feedback = backend.requests[1]["planning"]["feedback"]
        self.assertIn("no Noble Phantasm", feedback)
        self.assertIn("outlast its HP", feedback)
        self.assertIsNotNone(policy._battle_plan, "the second answer stands")


class PlanInstructionScopeTests(unittest.TestCase):
    """Plan instructions carry mechanics only; strategy comes from the guide priorities."""

    def test_no_strategy_or_seal_prose_in_the_plan_instructions(self):
        text = PlannerPolicy._PLAN_INSTRUCTIONS
        for mechanic in ("s{field slot}-{skill}-t{target}", "cooldown", "re-planned", "priorities"):
            self.assertIn(mechanic, text)
        for strategy in ("Seal", "hedge", "np_role", "defensive", "outlast", "normal attacks"):
            self.assertNotIn(strategy, text)


class PlanBudgetTests(unittest.TestCase):
    """Live 2026-09-24 (solo Gareth): revisions were counted against the plan budget,
    so it ran out by turn 5 and every later action cost its own model call."""

    def test_a_revision_does_not_spend_a_plan(self):
        lazy = [{"turn": 1, "wave": 1, "skills": [], "np": []}]
        policy = PlannerPolicy(DATA, Backend(lazy, lazy), plan_ahead=True)
        policy.next_action(tactical("t1", 1, ready=()))
        self.assertEqual(1, policy._battle_plans_made)

    def test_a_lone_allowed_action_needs_no_model_call(self):
        backend = Backend()
        policy = PlannerPolicy(DATA, backend)
        action = policy.next_action(tactical("t1", 1, ready=()))
        self.assertEqual("attack", action.kind)
        self.assertEqual([], backend.requests)
        self.assertEqual("only_allowed_action", policy.last_decision["source"])


class FocusServantTests(unittest.TestCase):
    """User feedback 2026-09-25 (Morgan rematch): Ciel carried time-limited buffs,
    yet the chain spent its slots elsewhere. A plan's focus servant fills the chain."""

    def frame(self):
        faces = [{"slot": slot, "type": kind, "sealed": False, "servant": owner}
                 for slot, kind, owner in ((1, "buster", 1), (2, "buster", 1), (3, "arts", 2),
                                           (4, "arts", 2), (5, "quick", 2))]
        faces.append({"slot": 6, "type": "np", "sealed": False, "ready": True, "servant": 1})
        return Observation("c", "cards", "now", cards=faces, wave=1, turn=1)

    def test_the_focus_servants_face_cards_fill_the_chain(self):
        plan = [{"turn": 1, "wave": 1, "skills": [], "np": [], "focus": 2}]
        policy = PlannerPolicy(DATA, Backend(plan, plan), plan_ahead=True)
        policy.next_action(tactical("t1", 1, ready=()))
        chosen = policy.next_action(self.frame())
        self.assertEqual({3, 4, 5}, set(chosen.cards))

    def test_a_plan_without_focus_is_still_valid(self):
        plan = [{"turn": 1, "wave": 1, "skills": [], "np": []}]
        policy = PlannerPolicy(DATA, Backend(plan, plan), plan_ahead=True)
        policy.next_action(tactical("t1", 1, ready=()))
        self.assertIsNone(policy._battle_plan["turns"][1]["focus"])

    def test_the_schema_asks_for_a_focus(self):
        entry = PlannerPolicy._battle_plan_schema()["properties"]["turns"]["items"]
        self.assertIn("focus", entry["required"])


class NpOrderTests(unittest.TestCase):
    """Live 2026-09-25 (Morgan): Oberon's NP (Invincible [Demerit] to all enemies) fired
    first, so the two NPs after it barely hurt the boss. Plan order is honoured and an
    enemy-buffing NP always goes last."""

    DEMERIT = {10: {"noble_phantasms": [{"description": "Deal heavy damage to all enemies & inflict Sleep "
                                                         "(1 turn) & apply Invincible (1 turn) [Demerit] to all enemies"}]}}

    def cards(self):
        faces = [{"slot": slot, "type": kind, "sealed": False, "servant": 1 + slot % 2}
                 for slot, kind in ((1, "buster"), (2, "arts"), (3, "quick"), (4, "arts"), (5, "buster"))]
        faces += [{"slot": 6, "type": "np", "sealed": False, "ready": True, "servant": 1},
                  {"slot": 7, "type": "np", "sealed": False, "ready": True, "servant": 2}]
        allies = [{"slot": 1, "id": 10, "hp": 100, "np": 100}, {"slot": 2, "id": 11, "hp": 100, "np": 100}]
        return Observation("c", "cards", "now", cards=faces, allies=allies, wave=1, turn=1)

    def choose(self, plan, catalog=None):
        policy = PlannerPolicy(DATA, Backend(plan, plan), plan_ahead=True, strategy_catalog=catalog)
        policy.next_action(tactical("t1", 1, ready=()))
        return list(policy.next_action(self.cards()).cards)

    def test_an_np_that_buffs_the_enemies_fires_last(self):
        chosen = self.choose([{"turn": 1, "wave": 1, "skills": [], "np": [1, 2]}], self.DEMERIT)
        self.assertEqual(6, chosen[-1], chosen)
        self.assertIn(7, chosen)

    def test_the_planned_np_order_is_kept(self):
        chosen = self.choose([{"turn": 1, "wave": 1, "skills": [], "np": [2, 1]}])
        self.assertLess(chosen.index(7), chosen.index(6), chosen)


class NpDemeritTests(unittest.TestCase):
    """User feedback 2026-09-25: dangerous NPs (Arash kills himself, Chen Gong sacrifices
    an ally, Oberon buffs the enemies) must be impossible to miss."""

    def test_demerit_clauses_are_pulled_out_of_the_np_text(self):
        catalog = {"noble_phantasms": [{"description": "Deal significant damage to all enemies  + Sacrifice one "
                                                       "ally other than yourself (apply Death) [Demerit]"}]}
        self.assertEqual(["Sacrifice one ally other than yourself (apply Death) [Demerit]"],
                         PlannerPolicy._np_demerits(catalog))
        self.assertEqual([], PlannerPolicy._np_demerits({"noble_phantasms": [{"description": "Deal damage"}]}))

    def test_the_request_carries_np_demerits(self):
        catalog = {10: {"noble_phantasms": [{"description": "Deal damage to all enemies + Apply Death to "
                                                            "yourself [Demerit]"}]}}
        request = PlannerPolicy(DATA, Backend(PLAN), strategy_catalog=catalog).build_request(tactical("t1", 1))
        self.assertEqual(["Apply Death to yourself [Demerit]"], request["servant_metadata"][0]["np_demerits"])


class NowOrNeverTests(unittest.TestCase):
    """User feedback 2026-09-25: Oberon's kit is a final attack (charge drained and
    Eternal Slumber at the end of the turn); casting half of it without the target's
    NP that turn left the team in an awkward position."""

    CATALOG = {10: {"skills": [{"slot": 2, "variants": [{"description": 'Increase NP Gauge & apply "decrease NP '
                                                                          'Gauge at the end of the turn for yourself '
                                                                          '(1 time)" for a single ally'}]},
                               {"slot": 3, "variants": [{"description": "Increase ATK (3 turns) for yourself"}]}]}}

    def issue(self, turns):
        policy = PlannerPolicy(DATA, Backend(PLAN), plan_ahead=True, strategy_catalog=self.CATALOG)
        frame = tactical("t1", 1)
        frame.allies[:] = [ally(1), {**ally(2), "id": 11}]
        return policy._now_or_never_issue(turns, frame)

    def test_a_final_attack_skill_without_that_turns_np_is_flagged(self):
        text = self.issue({1: {"wave": 1, "skills": ["s1-2-t2"], "np": [1]}})
        self.assertIn("s1-2-t2 on turn 1", text)

    def test_the_same_skill_with_the_targets_np_is_fine(self):
        self.assertIsNone(self.issue({1: {"wave": 1, "skills": ["s1-2-t2"], "np": [2]}}))

    def test_ordinary_skills_are_not_flagged(self):
        self.assertIsNone(self.issue({1: {"wave": 1, "skills": ["s1-3-t1"], "np": []}}))


class ShortlistDemeritTests(unittest.TestCase):
    """Live 2026-09-25 (3x Morgan): per-action mode listed only [Oberon NP, Ciel NP, card]
    for that NP pair, so firing both meant making the boss Invincible first."""

    def test_the_enemy_buffing_np_is_last_in_every_listed_triple(self):
        observation = NpOrderTests().cards()
        request = PlannerPolicy(DATA, Backend(), strategy_catalog=NpOrderTests.DEMERIT).build_request(observation)
        with_six = [c["cards"] for c in request["card_candidates"] if 6 in c["cards"]]
        self.assertTrue(with_six)
        self.assertTrue(all(cards[-1] == 6 for cards in with_six), with_six)
        self.assertIn(frozenset({6, 7}), {frozenset(s for s in c if s >= 6) for c in with_six})


class ExpectedChargeRateTests(unittest.TestCase):
    """Live 2026-09-25 (3x Morgan): +3 charge every turn re-planned every turn."""

    def test_a_jump_at_the_measured_rate_is_not_a_new_crisis(self):
        frame = lambda turn, charge: Observation(f"t{turn}", "tactical", "now", allies=[ally(ready=())], wave=1,
                                                 turn=turn, enemies=[{"slot": 1, "hp": 200000, "charge": [charge, 9]}],
                                                 signals={"stage_total": 1})
        rows = lambda first: [{"turn": t, "wave": 1, "skills": [], "np": [1]} for t in range(first, 9)]
        backend = Backend(rows(1), rows(2), rows(3))
        policy = PlannerPolicy(DATA, backend, plan_ahead=True)
        policy.next_action(frame(1, 0))
        policy.next_action(cards("c1", 1))
        policy.next_action(frame(2, 3))
        policy.next_action(cards("c2", 2))
        policy.next_action(frame(3, 6))
        self.assertEqual(2, len(backend.requests), "the second +3 matches the measured rate")


class HordeRespawnTests(unittest.TestCase):
    """Live 2026-09-25 (Mors horde): every spawn into a slot forced a crisis re-plan."""

    def frame(self, turn, hp, remaining):
        return Observation(f"t{turn}", "tactical", "now", allies=[ally(ready=())], wave=1, turn=turn,
                           enemies=[{"slot": 1, "hp": hp}],
                           signals={"stage_total": 1, "enemies_remaining": remaining})

    def replans(self, remaining_after):
        rows = lambda first: [{"turn": t, "wave": 1, "skills": [], "np": [1]} for t in range(first, 9)]
        backend = Backend(rows(1), rows(2))
        policy = PlannerPolicy(DATA, backend, plan_ahead=True)
        policy.next_action(self.frame(1, 20000, 10))
        policy.next_action(cards("c1", 1))
        policy.next_action(self.frame(2, 90000, remaining_after))
        return len(backend.requests) - 1

    def test_a_new_spawn_after_kills_is_not_a_refill(self):
        self.assertEqual(0, self.replans(remaining_after=8))

    def test_an_hp_rise_with_the_same_enemy_count_is_still_a_refill(self):
        self.assertEqual(1, self.replans(remaining_after=10))


class EarlyNpTests(unittest.TestCase):
    """Live 2026-09-25 (Red Dragon): two damaging NPs filled a turn early; the schedule
    fired nothing for three turns and the party died with both gauges full."""

    CATALOG = {10: {"noble_phantasms": [{"description": "Deal significant damage to all enemies"}]}}

    def run_turn_two(self, np_now):
        rows = lambda first: [{"turn": t, "wave": 1, "skills": [], "np": [1] if t == 3 else []} for t in range(first, 5)]
        backend = Backend(rows(1), rows(1), rows(2), rows(2))
        policy = PlannerPolicy(DATA, backend, plan_ahead=True, strategy_catalog=self.CATALOG)
        policy.next_action(tactical("t1", 1, ready=()))
        policy.next_action(cards("c1", 1, np_ready=False))
        frame = tactical("t2", 2, ready=())
        frame.allies[0]["np"] = np_now
        policy.next_action(frame)
        return backend.requests

    def test_a_ready_offensive_np_the_schedule_skips_replans(self):
        requests = self.run_turn_two(np_now=100)
        replans = [r for r in requests if r.get("planning", {}).get("deviation")]
        self.assertTrue(replans)
        self.assertIn("offensive NP of slot(s) [1] is ready", replans[0]["planning"]["deviation"])

    def test_an_unready_gauge_keeps_the_schedule(self):
        requests = self.run_turn_two(np_now=40)
        self.assertFalse([r for r in requests if r.get("planning", {}).get("deviation")])


class DefenceReadingTests(unittest.TestCase):
    """Live 2026-09-26 (Red Dragon): Castoria's Sword of Selection (Arts up 3 turns +
    single-ally Invincible) was held as the defensive answer; her NP's "anti-Enforcement
    DEF state" was not seen as defence; Draco's own charge raise filled the dragon early."""

    CATALOG = {10: {"skills": [{"slot": 1, "variants": [{"description": "Increase Arts Card effectiveness (3 turns) "
                                                                        "& apply Invincible (1 turn) for a single ally"}]},
                               {"slot": 2, "variants": [{"description": "Inflict Burn (3 turns) & increase Charge "
                                                                        "[Demerit] for all enemies + Increase ATK (3 turns)"}]},
                               {"slot": 3, "variants": [{"description": "Apply Invincible (1 turn) to all allies"}]}],
                    "noble_phantasms": [{"description": "Increase ATK (3 turns) & apply an anti-Enforcement DEF "
                                                        "state (1 time, 3 turns) for all allies"}]}}

    def policy(self):
        return PlannerPolicy(DATA, Backend(PLAN), plan_ahead=True, strategy_catalog=self.CATALOG)

    def test_an_invincibility_bundled_with_a_lasting_buff_is_not_the_reserve(self):
        frame = tactical("t1", 1, ready=(1, 2, 3))
        skills, nps = self.policy()._defensive_options(frame)
        self.assertEqual({"s1-3"}, skills)
        self.assertEqual({1}, nps, "a DEF-state NP is protection")

    def test_the_plans_own_charge_raise_moves_the_enemy_np_earlier(self):
        frame = Observation("t1", "tactical", "now", allies=[ally()], wave=1, turn=1,
                            enemies=[{"slot": 1, "hp": 500000, "charge": [3, 4]}], signals={"stage_total": 1})
        policy = self.policy()
        turns = {1: {"wave": 1, "skills": ["s1-2-t1"], "np": []}, 2: {"wave": 1, "skills": [], "np": []}}
        raises = policy._planned_charge_raises(turns, frame)
        self.assertEqual({1: 1}, raises)
        self.assertEqual([(1, 1)], PlannerPolicy._danger_turns(frame, raises))
        self.assertEqual([(2, 1)], PlannerPolicy._danger_turns(frame))


class SkillAfterOrderChangeTests(unittest.TestCase):
    """Live 2026-09-26: Koyanskaya's s1-3 before an Order Change and Oberon's s1-3 after
    it were rejected as a repeated skill, and the turn fell back to per-action play."""

    def test_the_same_slot_skill_is_valid_again_after_an_order_change(self):
        policy = PlannerPolicy(DATA, Backend(PLAN), plan_ahead=True)
        request = policy.build_request(tactical("t1", 1))
        allowed = policy._allowed_actions(tactical("t1", 1))
        response = {"observation_id": request["observation_id"], "request_id": request["request_id"],
                    "reason": "", "uncertainty": [],
                    "turns": [{"turn": 1, "wave": 1, "skills": ["s1-3-t1", "oc-f1-b1", "s1-3-t1"], "np": []}]}
        self.assertEqual(3, len(policy._validate_battle_plan(response, request, tactical("t1", 1), allowed)[1]["skills"]))
        response["turns"][0]["skills"] = ["s1-3-t1", "s1-3-t1"]
        with self.assertRaises(Exception):
            policy._validate_battle_plan(response, request, tactical("t1", 1), allowed)

    def test_an_issued_step_is_keyed_by_the_servant_in_the_slot(self):
        before = tactical("t1", 1)
        after = tactical("t1", 1)
        after.allies[0]["id"] = 316
        self.assertNotEqual(PlannerPolicy._issue_key(before, "s1-3-t3"), PlannerPolicy._issue_key(after, "s1-3-t3"))


class EnemyPressureTests(unittest.TestCase):
    """Live 2026-09-27 (Cernunnos): turn-1 normal attacks killed the damage dealer
    while party invincibility was held for a turn-6 enemy NP."""

    def frame(self, name, turn, hps, ids=(10, 10, 10)):
        allies = [{"slot": s + 1, "id": ids[s], "hp": hp} for s, hp in enumerate(hps)]
        return Observation(name, "tactical", "now", wave=1, turn=turn, allies=allies,
                           enemies=[{"slot": 2, "hp": 100000, "class_name": "berserker"}])

    def test_losses_over_the_enemy_turn_and_class_multipliers(self):
        policy = PlannerPolicy(DATA, Backend(PLAN))
        policy._track_incoming(self.frame("t1-start", 1, (18000, 17000, 15500)))
        policy._track_incoming(self.frame("t1-end", 1, (18500, 17000, 15500)))
        policy._track_incoming(self.frame("t2", 2, (2800, 2000, 14000), ids=(10, 10, 37)))
        rows = {r["slot"]: r for r in policy._incoming["allies"]}
        self.assertEqual(15700, rows[1]["lost"])
        self.assertTrue(rows[3]["died"])
        policy._class_relation = {"berserker": {"assassin": 2000}}
        policy._catalog_entry = lambda ident: {"class_name": "assassin"}
        view = policy._enemy_pressure_view(self.frame("t2", 2, (2800, 2000, 14000)))
        self.assertEqual(2.0, view["enemy_class_damage_multiplier"][0]["vs_ally_slot"]["1"])


class LethalDamageTests(unittest.TestCase):
    """Live 2026-09-27 (Cernunnos): allies at 1-3k HP after losing 5-10k each; the
    plan fired an NP and held party invincibility for the next turn."""

    def setup(self, hp=1200, lost=6000):
        policy = PlannerPolicy(DATA, Backend(PLAN), strategy_catalog=DefensiveReserveTests.CATALOG,
                               plan_ahead=True)
        policy._incoming = {"enemy_turn_after_turn": 1, "allies": [
            {"slot": 1, "servant_id": 10, "hp_before": hp + lost, "hp_after": hp, "lost": lost}]}
        low = dict(ally(ready=(1, 3)), hp=hp)
        obs = Observation("t2", "tactical", "now", allies=[low], wave=1, turn=2,
                          enemies=[{"slot": 1, "hp": 200000, "charge": [1, 5]}], signals={"stage_total": 1})
        return policy, obs

    def test_an_unused_ready_defence_is_flagged(self):
        policy, obs = self.setup()
        issue = policy._lethal_issue({2: {"wave": 1, "skills": ["s1-3-t1"], "np": [1]}}, obs)
        self.assertIn("slot 1 (1200 HP, lost 6000)", issue)
        self.assertIsNone(policy._lethal_issue({2: {"wave": 1, "skills": ["s1-1-t1"], "np": []}}, obs))

    def test_survivable_losses_are_not_flagged(self):
        policy, obs = self.setup(hp=9000, lost=6000)
        self.assertIsNone(policy._lethal_issue({2: {"wave": 1, "skills": [], "np": [1]}}, obs))
