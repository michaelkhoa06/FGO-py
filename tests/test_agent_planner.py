import sys
import unittest
import tempfile
import hashlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "FGO-py"))

from fgo_agent import Action, ActionResult, Observation
from fgo_agent.planner import PlannerError, PlannerPolicy


DATA = {10: (0, 0, 0, 0, (0, 0), ((1, 1), (1, 6), (1, 0)))}


class Backend:
    def __init__(self, reply):
        self.reply = reply
        self.last_call = {"model": "sol", "token": "hidden"}

    def choose(self, request, schema):
        if callable(self.reply):
            return self.reply(request, schema)
        return self.reply


def obs(identifier="o", phase="tactical", **kwargs):
    return Observation(identifier, phase, "now", **kwargs)


def ally(slot=1, ident=10, hp=100, np=0, ready=(1,)):
    return {"slot": slot, "id": ident, "hp": hp, "np": np,
            "skills": [{"slot": index, "ready": index in ready} for index in (1, 2, 3)]}


def choose(identifier):
    return lambda request, schema: {"observation_id": request["observation_id"], "request_id": request["request_id"],
                                    "action_id": identifier, "reason": "bounded choice", "uncertainty": []}


class PlannerPolicyTests(unittest.TestCase):
    def test_fresh_policy_loads_persisted_guide_and_binds_its_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'PLAYBOOK.md'
            path.write_text('Persisted rule one', encoding='utf-8')
            first = PlannerPolicy(DATA, Backend(choose('attack')), playbook_path=path)
            request = first.build_request(obs(allies=[ally()], wave=1, turn=1))
            self.assertEqual('Persisted rule one', request['instructions'])
            self.assertEqual(hashlib.sha256(b'Persisted rule one').hexdigest(), request['strategy_guide_sha256'])
            path.write_text('Persisted rule two', encoding='utf-8')
            fresh = PlannerPolicy(DATA, Backend(choose('attack')), playbook_path=path)
            second = fresh.build_request(obs(allies=[ally()], wave=1, turn=1))
            self.assertEqual('Persisted rule two', second['instructions'])
            self.assertNotEqual(request['request_id'], second['request_id'])

    def test_affinity_weak_is_explicitly_enemy_vulnerability(self):
        self.assertEqual('enemy_vulnerable_bonus_damage', PlannerPolicy._card_view({'affinity':'weak'})['affinity_meaning'])
        self.assertEqual('enemy_resists_reduced_damage', PlannerPolicy._card_view({'affinity':'resist'})['affinity_meaning'])
        self.assertEqual('unknown', PlannerPolicy._card_view({'affinity':None})['affinity_meaning'])

    def test_malformed_effect_and_inconsistent_positive_cooldown_are_excluded(self):
        # Skill 1 has no usable tuple at all; skill 2 reports a positive cooldown
        # while claiming readiness. An unclassified-but-well-formed effect is a
        # separate case, covered by UnclassifiedSkillTests.
        data = {10: (0, 0, 0, 0, (1, 2), (('?', 0), (2, 1), (1, 1)))}
        a = ally(ready=(1, 2, 3))
        a['skills'][1]['cooldown'] = 5
        request = PlannerPolicy(data, Backend(choose('attack'))).build_request(obs(allies=[a, ally(2)], wave=1, turn=1))
        self.assertNotIn('s1-1-t1', [x['id'] for x in request['allowed_actions']])
        self.assertNotIn('s1-2-t1', [x['id'] for x in request['allowed_actions']])
        self.assertIn('s1-3-t1', [x['id'] for x in request['allowed_actions']])
        self.assertNotIn('s1-3-t2', [x['id'] for x in request['allowed_actions']])

    def test_np_semantics_and_visual_groups_are_available_without_identity_guess(self):
        catalog = {10: {'name': 'Example', 'noble_phantasms': [{'id': 1, 'description': 'Single target damage'}]}}
        request = PlannerPolicy(DATA, Backend(choose('attack')), strategy_catalog=catalog).build_request(
            obs(allies=[ally()], cards=[{'slot': 1, 'servant': None, 'visualgroupid': 7}], wave=1, turn=1))
        self.assertEqual('single_target', request['servant_metadata'][0]['np_kind'])
        self.assertEqual('Single target damage', request['servant_metadata'][0]['catalog_noble_phantasms'][0]['description'])
        self.assertIsNone(request['cards'][0]['servant'])
        self.assertEqual(7, request['cards'][0]['visualgroupid'])

    def test_malformed_response_requires_exact_shape(self):
        policy = PlannerPolicy(DATA, Backend(lambda request, schema: {"observation_id": request["observation_id"], "request_id": request["request_id"],
                                                                      "action_id": "attack", "reason": "x", "uncertainty": [], "extra": 1}))
        with self.assertRaises(PlannerError):
            policy.next_action(obs(allies=[ally()], wave=1, turn=1))

    def test_response_requires_request_binding(self):
        policy = PlannerPolicy(DATA, Backend(lambda request, schema: {"observation_id": "wrong", "request_id": request["request_id"],
                                                                      "action_id": "attack", "reason": "x", "uncertainty": []}))
        with self.assertRaises(PlannerError):
            policy.next_action(obs(allies=[ally()], wave=1, turn=1))

    def test_invalid_action_is_not_fallback(self):
        policy = PlannerPolicy(DATA, Backend(choose("enemy-1")))
        with self.assertRaises(PlannerError):
            policy.next_action(obs(allies=[ally()], wave=1, turn=1))

    def test_special_mode_and_unknown_or_dead_actors_are_excluded(self):
        policy = PlannerPolicy(DATA, Backend(choose("attack")))
        request = policy.build_request(obs(allies=[ally(1, 10, ready=(1, 2, 3)), ally(2, None), ally(3, 10, hp=None)], wave=1, turn=1))
        self.assertEqual(["attack", "s1-1-t1", "s1-3-t1"], [a["id"] for a in request["allowed_actions"]])

    def test_unknown_hp_and_np_remain_unknown_in_request(self):
        policy = PlannerPolicy(DATA, Backend(choose("attack")))
        request = policy.build_request(obs(allies=[ally(hp=None, np=None)], wave=1, turn=1))
        self.assertIsNone(request["allies"][0]["hp"])
        self.assertIsNone(request["allies"][0]["np"])
        self.assertEqual(["attack"], [item["id"] for item in request["allowed_actions"]])

    def test_cards_enumerate_permutations_then_rank_to_a_scored_shortlist(self):
        cards = [{"slot": 1, "type": "arts", "sealed": False}, {"slot": 2, "type": "quick", "sealed": False},
                 {"slot": 3, "type": "buster", "sealed": False}, {"slot": 4, "type": "np", "sealed": False, "ready": True},
                 {"slot": 5, "type": "np", "sealed": False, "ready": False}]
        policy = PlannerPolicy(DATA, Backend(choose("c1-2-3")))
        request = policy.build_request(obs("cards", "cards", cards=cards))
        ids = [item["id"] for item in request["allowed_actions"]]
        # 24 legal permutations are enumerated, then trimmed to a ranked shortlist.
        self.assertEqual(PlannerPolicy._MAX_CARD_CANDIDATES, len(ids))
        self.assertNotIn("c1-1-2", ids)
        candidates = request["card_candidates"]
        self.assertEqual(ids, [item["id"] for item in candidates])
        self.assertEqual(sorted((c["score"] for c in candidates), reverse=True),
                         [c["score"] for c in candidates])
        for candidate in candidates:
            self.assertTrue(set(candidate["cards"]) <= {1, 2, 3, 4})
            self.assertEqual(3, len(set(candidate["cards"])))

    def test_card_shortlist_keeps_every_id_legal_and_retains_the_baseline_choice(self):
        cards = [{"slot": slot, "type": kind, "sealed": False, "affinity": affinity}
                 for slot, kind, affinity in ((1, "arts", "resist"), (2, "quick", "resist"), (3, "buster", "weak"),
                                              (4, "buster", "weak"), (5, "arts", "weak"))]
        policy = PlannerPolicy(DATA, Backend(choose("c1-2-3")))
        observation = obs("cards", "cards", cards=cards)
        request = policy.build_request(observation)
        allowed = policy._allowed_actions(observation)
        ids = [item["id"] for item in request["allowed_actions"]]
        self.assertTrue(set(ids) <= set(allowed), "shortlist must stay a subset of enumerated legal actions")
        baseline = [c for c in request["card_candidates"] if c["baseline_choice"]]
        self.assertEqual(1, len(baseline), "the upstream heuristic choice must remain selectable")
        self.assertEqual(policy._baseline_card_id(observation), baseline[0]["id"])

    def test_every_set_of_ready_nps_stays_selectable(self):
        # Live 2026-09-25 (Morgan): two ready NPs, but no NP triple made the top ten.
        cards = [{"slot": slot, "type": kind, "sealed": False, "servant": servant}
                 for slot, kind, servant in ((1, "arts", 1), (2, "arts", 1), (3, "quick", 2),
                                             (4, "buster", 3), (5, "arts", 3))]
        cards += [{"slot": 7, "type": "np", "sealed": False, "ready": True, "servant": 2},
                  {"slot": 8, "type": "np", "sealed": False, "ready": True, "servant": 3}]
        request = PlannerPolicy(DATA, Backend(choose("c1-2-3"))).build_request(obs("cards", "cards", cards=cards))
        np_sets = {frozenset(slot for slot in c["cards"] if slot >= 6) for c in request["card_candidates"]}
        self.assertTrue({frozenset(), frozenset({7}), frozenset({8}), frozenset({7, 8})} <= np_sets, np_sets)

    def test_duplicate_card_ownership_does_not_make_visual_cards_illegal(self):
        cards = [{"slot": slot, "type": "arts", "servant": 1, "sealed": False} for slot in (1, 2, 3)]
        request = PlannerPolicy(DATA, Backend(choose("c1-2-3"))).build_request(obs("cards", "cards", cards=cards))
        self.assertEqual(["c1-2-3", "c1-3-2", "c2-1-3", "c2-3-1", "c3-1-2", "c3-2-1"],
                         [item["id"] for item in request["allowed_actions"]])

    def test_confirmed_and_unconfirmed_skills_are_both_withheld_for_the_turn(self):
        # An unconfirmed cast may have happened. Since the runner now continues
        # after one, re-offering it the same turn would risk a proposal loop.
        policy = PlannerPolicy(DATA, Backend(choose("s1-1-t1")))
        first = policy.next_action(obs("one", allies=[ally()], wave=1, turn=1))
        policy.observe_result(first, ActionResult(first, "uncertain", "one", None, skill_consumed=None))
        request = policy.build_request(obs("two", allies=[ally()], wave=1, turn=1))
        self.assertNotIn("s1-1-t1", [item["id"] for item in request["allowed_actions"]])
        other = PlannerPolicy(DATA, Backend(choose("s1-1-t1")))
        cast = other.next_action(obs("one", allies=[ally()], wave=1, turn=1))
        other.observe_result(cast, ActionResult(cast, "confirmed", "one", "three", skill_consumed=True))
        request = other.build_request(obs("three", allies=[ally()], wave=1, turn=1))
        self.assertNotIn("s1-1-t1", [item["id"] for item in request["allowed_actions"]])

    def test_exhausted_cards_raise_and_request_omits_screen_data(self):
        policy = PlannerPolicy(DATA, Backend(choose("attack")))
        with self.assertRaises(PlannerError):
            policy.next_action(obs("empty", "cards", cards=[{"slot": 1, "type": "arts", "sealed": False}]))
        request = policy.build_request(obs(allies=[ally()], wave=1, turn=1, screen_path="secret.png", signals={"stage_total": 3, "sha256": "frame"}))
        self.assertNotIn("screen_path", request)
        self.assertEqual({"stage_total": 3}, request["salient_signals"])

    def test_decision_records_sanitized_call_metadata(self):
        policy = PlannerPolicy(DATA, Backend(choose("attack")))
        policy.next_action(obs(allies=[ally()], wave=1, turn=1))
        self.assertEqual("sol", policy.last_decision["model_call_metadata"]["model"])
        self.assertNotIn("token", policy.last_decision["model_call_metadata"])

    def test_catalog_variants_are_read_only_context_for_matching_living_ally(self):
        catalog = {10: {"name": "Example", "skills": [{"slot": 1, "variants": [{"id": 4, "name": "Guard", "description": "Protect ally."}]}]}}
        request = PlannerPolicy(DATA, Backend(choose("attack")), strategy_catalog=catalog).build_request(
            obs(allies=[ally()], wave=1, turn=1))
        self.assertEqual("Example", request["servant_metadata"][0]["english_name"])
        self.assertEqual("Protect ally.", request["servant_metadata"][0]["catalog_skills"][0]["variants"][0]["description"])

    def test_enemy_targets_require_verified_known_living_nonselected_slots(self):
        policy = PlannerPolicy(DATA, Backend(choose("attack")))
        common = {"enemy_layout": "standard3", "enemy_target_verifiable": True, "selected_enemy": 1}
        request = policy.build_request(obs(wave=1, turn=1, enemies=[{"slot": 1, "hp": 100}, {"slot": 2, "hp": 100},
                                                                      {"slot": 3, "hp": 0}], signals=common))
        self.assertEqual(["attack", "e2"], [item["id"] for item in request["allowed_actions"]])
        unknown = policy.build_request(obs(wave=1, turn=1, enemies=[{"slot": 2, "hp": 100}],
                                           signals={"enemy_layout": "standard3", "enemy_target_verifiable": True,
                                                    "selected_enemy": None}))
        self.assertEqual(["attack"], [item["id"] for item in unknown["allowed_actions"]])
        self.assertEqual({"selected_enemy": 1, "enemy_layout": "standard3", "enemy_target_verifiable": True},
                         request["salient_signals"])

    def test_confirmed_enemy_target_change_prevents_retarget_pingpong_until_turn_changes(self):
        policy = PlannerPolicy(DATA, Backend(choose("e2")))
        common = {"enemy_layout": "standard3", "enemy_target_verifiable": True, "selected_enemy": 1}
        first = policy.next_action(obs("one", wave=1, turn=1, enemies=[{"slot": 1, "hp": 100}, {"slot": 2, "hp": 100}],
                                       signals=common))
        self.assertEqual("enemy_target", first.kind)
        self.assertEqual(2, first.target)
        policy.observe_result(first, ActionResult(first, "confirmed", "one", "two"))
        blocked = policy.build_request(obs("two", wave=1, turn=1, enemies=[{"slot": 1, "hp": 100}, {"slot": 2, "hp": 100}],
                                           signals={**common, "selected_enemy": 2}))
        self.assertEqual(["attack"], [item["id"] for item in blocked["allowed_actions"]])
        next_turn = policy.build_request(obs("three", wave=1, turn=2, enemies=[{"slot": 1, "hp": 100}, {"slot": 2, "hp": 100}],
                                             signals={**common, "selected_enemy": 2}))
        self.assertIn("e1", [item["id"] for item in next_turn["allowed_actions"]])

    def test_enemy_targets_require_positive_context_selected_hp_and_unambiguous_slots(self):
        policy = PlannerPolicy(DATA, Backend(choose("attack")))
        signals = {"enemy_layout": "standard3", "enemy_target_verifiable": True, "selected_enemy": 1}
        for wave, turn, enemies in [
            (0, 1, [{"slot": 1, "hp": 100}, {"slot": 2, "hp": 100}]),
            (1, -1, [{"slot": 1, "hp": 100}, {"slot": 2, "hp": 100}]),
            (1, 1, [{"slot": 1, "hp": 0}, {"slot": 2, "hp": 100}]),
            (1, 1, [{"slot": 1, "hp": 100}, {"slot": 2, "hp": 100}, {"slot": 2, "hp": 100}]),
            (1, 1, [{"slot": True, "hp": 100}, {"slot": 2, "hp": 100}]),
        ]:
            request = policy.build_request(obs(wave=wave, turn=turn, enemies=enemies, signals=signals))
            self.assertEqual(["attack"], [item["id"] for item in request["allowed_actions"]])


if __name__ == "__main__":
    unittest.main()


def plan(*identifiers):
    return lambda request, schema: {"observation_id": request["observation_id"], "request_id": request["request_id"],
                                    "plan": list(identifiers), "reason": "batched turn plan", "uncertainty": []}


class CountingBackend(Backend):
    def __init__(self, reply):
        super().__init__(reply)
        self.calls = 0

    def choose(self, request, schema):
        self.calls += 1
        return super().choose(request, schema)


def confirmed(action, consumed=True):
    return ActionResult(action, "confirmed", "before", "after", skill_consumed=consumed)


class TurnPlanTests(unittest.TestCase):
    """A batched plan must cost one model call yet stay bound to fresh observations."""

    def test_planned_actions_after_the_first_are_served_without_model_calls(self):
        backend = CountingBackend(plan("s1-1-t1", "s1-3-t1", "attack"))
        policy = PlannerPolicy(DATA, backend, objective="farm_fast")
        first = policy.next_action(obs("one", allies=[ally(ready=(1, 2, 3))], wave=1, turn=1))
        self.assertEqual(("skill", 1, 1), (first.kind, first.servant, first.skill))
        self.assertEqual(1, backend.calls)
        policy.observe_result(first, confirmed(first))
        second = policy.next_action(obs("two", allies=[ally(ready=(2, 3))], wave=1, turn=1))
        self.assertEqual(("skill", 1, 3), (second.kind, second.servant, second.skill))
        self.assertEqual(1, backend.calls, "a queued action must not call the model again")
        policy.observe_result(second, confirmed(second))
        third = policy.next_action(obs("three", allies=[ally(ready=())], wave=1, turn=1))
        self.assertEqual("attack", third.kind)
        self.assertEqual(1, backend.calls)
        self.assertEqual("queued_turn_plan", policy.last_decision["source"])

    def test_queued_action_is_dropped_when_the_fresh_screen_no_longer_offers_it(self):
        backend = CountingBackend(plan("s1-1-t1", "s1-3-t1", "attack"))
        policy = PlannerPolicy(DATA, backend)
        first = policy.next_action(obs("one", allies=[ally(ready=(1, 2, 3))], wave=1, turn=1))
        policy.observe_result(first, confirmed(first))
        # Skill 3 is no longer ready on the fresh frame, so the plan must skip to attack.
        served = policy.next_action(obs("two", allies=[ally(ready=())], wave=1, turn=1))
        self.assertEqual("attack", served.kind)
        self.assertEqual(1, backend.calls)

    def test_plan_does_not_survive_a_turn_change_or_an_unconfirmed_action(self):
        backend = CountingBackend(plan("s1-1-t1", "attack"))
        policy = PlannerPolicy(DATA, backend)
        first = policy.next_action(obs("one", allies=[ally(ready=(1, 2, 3))], wave=1, turn=1))
        policy.observe_result(first, confirmed(first))
        policy.next_action(obs("two", allies=[ally(ready=(1, 2, 3))], wave=1, turn=2))
        self.assertEqual(2, backend.calls, "a new turn must be planned freshly")
        backend.reply = plan("s1-1-t1", "attack")
        second = policy.next_action(obs("three", allies=[ally(ready=(1, 2, 3))], wave=2, turn=1))
        calls = backend.calls
        policy.observe_result(second, ActionResult(second, "rejected", "three", None, skill_consumed=None))
        backend.reply = plan("s1-3-t1", "attack")
        policy.next_action(obs("four", allies=[ally(ready=(1, 2, 3))], wave=2, turn=1))
        self.assertEqual(calls + 1, backend.calls, "an unconfirmed action must invalidate the queue")

    def test_plan_shape_rules_are_enforced(self):
        allies = [ally(ready=(1, 2, 3))]
        for reply, message in ((plan("attack", "s1-1-t1"), "no action may follow attack"),
                               (plan("s1-1-t1", "s1-1-t1"), "a repeated action is rejected"),
                               (plan(), "an empty plan is rejected")):
            with self.subTest(message):
                policy = PlannerPolicy(DATA, Backend(reply))
                with self.assertRaises(PlannerError):
                    policy.next_action(obs(allies=allies, wave=1, turn=1))

    def test_cards_action_must_be_planned_alone(self):
        cards = [{"slot": slot, "type": "arts", "sealed": False} for slot in (1, 2, 3)]
        policy = PlannerPolicy(DATA, Backend(plan("c1-2-3", "c1-3-2")))
        with self.assertRaises(PlannerError):
            policy.next_action(obs("cards", "cards", cards=cards))

    def test_single_action_id_responses_remain_valid(self):
        policy = PlannerPolicy(DATA, Backend(choose("attack")))
        self.assertEqual("attack", policy.next_action(obs(allies=[ally()], wave=1, turn=1)).kind)


class ObjectiveTests(unittest.TestCase):
    def test_objective_is_declared_in_the_request_and_defaults_to_survival(self):
        self.assertEqual({"objective": "survive_and_win"},
                         PlannerPolicy(DATA, Backend(choose("attack"))).build_request(
                             obs(allies=[ally()], wave=1, turn=1))["quest_context"])
        self.assertEqual({"objective": "farm_fast"},
                         PlannerPolicy(DATA, Backend(choose("attack")), objective="farm_fast").build_request(
                             obs(allies=[ally()], wave=1, turn=1))["quest_context"])

    def test_unknown_objective_is_rejected(self):
        with self.assertRaises(PlannerError):
            PlannerPolicy(DATA, Backend(choose("attack")), objective="win_at_all_costs")


class WaveFeedbackTests(unittest.TestCase):
    """The model must be able to see that a wave already survived a card turn."""

    def test_stage_turn_is_passed_through_as_a_salient_signal(self):
        policy = PlannerPolicy(DATA, Backend(choose("attack")), objective="farm_fast")
        request = policy.build_request(obs(allies=[ally()], wave=2, turn=3,
                                          signals={"stage_turn": 2, "stage_total": 3}))
        self.assertEqual(2, request["salient_signals"]["stage_turn"])

    def test_absent_stage_turn_is_not_invented(self):
        policy = PlannerPolicy(DATA, Backend(choose("attack")), objective="farm_fast")
        request = policy.build_request(obs(allies=[ally()], wave=1, turn=1, signals={"stage_total": 3}))
        self.assertNotIn("stage_turn", request["salient_signals"])


class UnclassifiedSkillTests(unittest.TestCase):
    """An unclassified upstream effect must stay usable when targeting is safe."""

    def test_effect_class_zero_is_offered_with_explicit_provenance(self):
        data = {10: (0, 0, 0, 0, (1, 2), ((0, 1), (1, 5), (0, 6)))}
        policy = PlannerPolicy(data, Backend(choose("attack")), objective="farm_fast")
        request = policy.build_request(obs(allies=[ally(ready=(1, 2, 3))], wave=3, turn=3))
        ids = [item["id"] for item in request["allowed_actions"]]
        self.assertIn("s1-1-t1", ids, "a class-0 skill with an ordinary mode must be selectable")
        self.assertNotIn("s1-3-t1", ids, "a special target mode stays excluded whatever the class")
        semantics = {row["slot"]: row["effect_class_semantics"]
                     for row in request["servant_metadata"][0]["legacy_skills"]}
        self.assertEqual("unknown_upstream_rely_on_description", semantics[1])
        self.assertEqual("coarse_upstream_category", semantics[2])

    def test_malformed_or_out_of_range_effects_are_still_excluded(self):
        data = {10: (0, 0, 0, 0, (1, 2), ((10, 1), (-1, 1), ("x", 1)))}
        policy = PlannerPolicy(data, Backend(choose("attack")))
        request = policy.build_request(obs(allies=[ally(ready=(1, 2, 3))], wave=1, turn=1))
        self.assertEqual(["attack"], [item["id"] for item in request["allowed_actions"]])


class PlanCapacityTests(unittest.TestCase):
    def test_a_full_setup_turn_plus_attack_fits_in_one_plan(self):
        # Three servants with three ready skills each can justify a six-skill
        # setup turn; the cap must leave room for the terminal attack too.
        self.assertGreaterEqual(PlannerPolicy._MAX_PLAN, 7)

    def test_plan_longer_than_the_cap_is_rejected(self):
        data = {10: (0, 0, 0, 0, (1, 2), ((1, 1), (1, 5), (1, 0)))}
        over = ["s1-1-t1"] * (PlannerPolicy._MAX_PLAN + 1)
        policy = PlannerPolicy(data, Backend(plan(*over)))
        with self.assertRaises(PlannerError):
            policy.next_action(obs(allies=[ally(ready=(1, 2, 3))], wave=1, turn=1))


class AttackPowerReferenceTests(unittest.TestCase):
    """Buff targeting needs class-adjusted attack, not raw attack."""

    CATALOG = {
        10: {'name': 'Berserker DPS', 'class_name': 'berserker', 'atk_max': 11669,
             'class_attack_rate': 1.1, 'effective_atk_max': 12836, 'hp_max': 13837},
        11: {'name': 'Assassin Support', 'class_name': 'assassin', 'atk_max': 11976,
             'class_attack_rate': 0.9, 'effective_atk_max': 10778, 'hp_max': 13081},
    }

    def test_effective_and_raw_attack_are_both_exposed(self):
        data = {10: (0, 0, 0, 0, (1, 2), ((1, 1),)), 11: (0, 0, 0, 0, (1, 2), ((1, 1),))}
        policy = PlannerPolicy(data, Backend(choose('attack')), strategy_catalog=self.CATALOG,
                               objective='farm_fast')
        request = policy.build_request(obs(allies=[ally(1, 10), ally(2, 11)], wave=1, turn=1))
        rows = {row['servant_id']: row for row in request['servant_metadata']}
        self.assertEqual(12836, rows[10]['catalog_effective_atk_max'])
        self.assertEqual(10778, rows[11]['catalog_effective_atk_max'])
        self.assertEqual(1.1, rows[10]['catalog_class_attack_rate'])
        self.assertEqual(13837, rows[10]['catalog_hp_max'])
        # The raw value alone would rank the support above the damage dealer.
        self.assertGreater(rows[11]['catalog_atk_max'], rows[10]['catalog_atk_max'])
        self.assertGreater(rows[10]['catalog_effective_atk_max'], rows[11]['catalog_effective_atk_max'])

    def test_missing_attack_reference_stays_absent(self):
        policy = PlannerPolicy({10: (0, 0, 0, 0, (1, 2), ((1, 1),))}, Backend(choose('attack')),
                               strategy_catalog={10: {'name': 'No stats'}})
        row = policy.build_request(obs(allies=[ally()], wave=1, turn=1))['servant_metadata'][0]
        self.assertIsNone(row['catalog_effective_atk_max'])
        self.assertIsNone(row['catalog_hp_max'])


class ShippedGuideTests(unittest.TestCase):
    def test_the_shipped_guide_loads_within_the_planner_size_guard(self):
        # Growing PLAYBOOK.md past the guard makes every live request fail at
        # construction, which no synthetic-guide test would notice.
        policy = PlannerPolicy(DATA, Backend(choose("attack")), objective="farm_fast")
        self.assertIn("three-turn procedure", policy._guide)


class OrderChangePlanningTests(unittest.TestCase):
    BACKLINE = [{"slot": 1, "name": "Oberon", "servant_id": 316},
                {"slot": 2, "name": "Space Ereshkigal", "servant_id": None}]

    def _obs(self, available=True):
        return obs(allies=[ally(1, 10, ready=()), ally(3, 10, ready=())], wave=3, turn=3,
                   signals={"order_change_available": available, "backline": self.BACKLINE})

    def test_swaps_are_offered_only_when_the_frame_says_available(self):
        policy = PlannerPolicy(DATA, Backend(choose("attack")), objective="farm_fast",
                               strategy_catalog={316: {"name": "Oberon", "class_name": "pretender",
                                                       "effective_atk_max": 11810}})
        ids = [a["id"] for a in policy.build_request(self._obs())["allowed_actions"]]
        self.assertIn("oc-f1-b1", ids)
        self.assertIn("oc-f3-b2", ids)
        request = policy.build_request(self._obs())
        self.assertEqual("resolved", request["backline"][0]["identity"])
        self.assertEqual(11810, request["backline"][0]["catalog_effective_atk_max"])
        self.assertEqual("unresolved_name_only", request["backline"][1]["identity"])
        closed = [a["id"] for a in policy.build_request(self._obs(False))["allowed_actions"]]
        self.assertFalse(any(i.startswith("oc-") for i in closed))

    def test_a_swap_must_end_the_plan(self):
        policy = PlannerPolicy(DATA, Backend(plan("oc-f1-b1", "attack")))
        with self.assertRaises(PlannerError):
            policy.next_action(self._obs())
        ok = PlannerPolicy(DATA, Backend(plan("oc-f1-b1")))
        action = ok.next_action(self._obs())
        self.assertEqual(("order_change", 1, 1), (action.kind, action.servant, action.target))


class UnreadableHpLivenessTests(unittest.TestCase):
    def test_alive_flag_keeps_an_unreadable_hp_servant_actionable(self):
        a = ally(ready=(1,), hp=None)
        a["alive"] = True
        ids = [x["id"] for x in PlannerPolicy(DATA, Backend(choose("attack"))).build_request(
            obs(allies=[a], wave=1, turn=1))["allowed_actions"]]
        self.assertIn("s1-1-t1", ids)

    def test_unknown_hp_without_the_alive_flag_stays_excluded(self):
        ids = [x["id"] for x in PlannerPolicy(DATA, Backend(choose("attack"))).build_request(
            obs(allies=[ally(ready=(1,), hp=None)], wave=1, turn=1))["allowed_actions"]]
        self.assertEqual(["attack"], ids)

    def test_known_zero_hp_is_dead_even_if_flagged(self):
        a = ally(ready=(1,), hp=0)
        a["alive"] = True
        ids = [x["id"] for x in PlannerPolicy(DATA, Backend(choose("attack"))).build_request(
            obs(allies=[a], wave=1, turn=1))["allowed_actions"]]
        self.assertEqual(["attack"], ids)


class StageNotesTests(unittest.TestCase):
    def test_notes_are_sent_framed_as_guidance_and_absent_without_them(self):
        policy = PlannerPolicy(DATA, Backend(choose("attack")), notes="Kill the Caster first; it NPs on turn 3.")
        request = policy.build_request(obs(allies=[ally()], wave=1, turn=1))
        self.assertEqual("Kill the Caster first; it NPs on turn 3.", request["stage_notes"]["text"])
        self.assertIn("not an observation", request["stage_notes"]["status"])
        plain = PlannerPolicy(DATA, Backend(choose("attack"))).build_request(obs(allies=[ally()], wave=1, turn=1))
        self.assertNotIn("stage_notes", plain)

    def test_oversized_notes_are_refused(self):
        with self.assertRaises(PlannerError):
            PlannerPolicy(DATA, Backend(choose("attack")), notes="x" * (PlannerPolicy._MAX_NOTES + 1))


class UnconfirmedSkillTests(unittest.TestCase):
    def test_an_unconfirmed_skill_is_withheld_for_the_rest_of_that_turn_only(self):
        policy = PlannerPolicy(DATA, Backend(choose("s1-1-t1")))
        first = policy.next_action(obs("one", allies=[ally(ready=(1, 3))], wave=1, turn=1))
        policy.observe_result(first, ActionResult(first, "uncertain", "one", None))
        same_turn = [a["id"] for a in policy.build_request(
            obs("two", allies=[ally(ready=(1, 3))], wave=1, turn=1))["allowed_actions"]]
        self.assertNotIn("s1-1-t1", same_turn)
        self.assertIn("s1-3-t1", same_turn)
        policy.backend = Backend(choose("attack"))
        policy.next_action(obs("three", allies=[ally(ready=(1, 3))], wave=1, turn=2))
        next_turn = [a["id"] for a in policy.build_request(
            obs("four", allies=[ally(ready=(1, 3))], wave=1, turn=2))["allowed_actions"]]
        self.assertIn("s1-1-t1", next_turn, "the guard must not outlive its turn")
