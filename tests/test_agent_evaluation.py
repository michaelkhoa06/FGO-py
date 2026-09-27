import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'FGO-py'))
from fgo_agent.catalog import normalize_servant
from fgo_agent.evaluation import load_cases, load_legacy_metadata, meets_objective
from fgo_agent.models import Action


class EvaluationTests(unittest.TestCase):
    def test_catalog_keeps_upgrade_alternatives_and_unknown_levels(self):
        record = normalize_servant({'collectionNo':1,'name':'Example','skills':[
            {'id':10,'num':1,'name':'Old','detail':'Defense'},
            {'id':11,'num':1,'name':'Upgrade','detail':'More defense'}]},'source','hash','time')
        self.assertEqual(2,len(record['skills'][0]['variants']))
        self.assertNotIn('skill_level',record)
        self.assertIn('unknown', ' '.join(record['limitations']))

    def test_frozen_scenarios_and_actual_metadata_load_without_upstream_runtime(self):
        root = Path(__file__).resolve().parents[1]
        cases = load_cases(root/'examples/strategy-scenarios.json')
        self.assertEqual(11,len(cases))
        self.assertTrue(all(case['objective'] for case in cases))
        self.assertEqual((2,1),load_legacy_metadata()[314][5][0])

    def test_objective_scoring_distinguishes_wrong_target_and_sealed_np(self):
        objective = {'fields':{'kind':'skill','target':3}}
        self.assertFalse(meets_objective(Action('skill',target=1),objective))
        self.assertTrue(meets_objective(Action('skill',target=3),objective))
        self.assertFalse(meets_objective(Action('cards',cards=(8,1,2)),{'cards_exclude':[8]}))

    def test_trace_loader_uses_only_preceding_decision_observation(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'trace.jsonl'
            rows=[{'event':'decision','payload':{'observation_id':'future'}},
                  {'event':'observation','payload':{'id':'future'}},
                  {'event':'decision','payload':{'observation_id':'future'}}]
            path.write_text('\n'.join(json.dumps(row) for row in rows))
            self.assertEqual(1,len(load_cases(path,trace=True)))


if __name__=='__main__': unittest.main()


class PlanObjectiveTests(unittest.TestCase):
    def test_plan_includes_any_checks_the_whole_planned_turn(self):
        from fgo_agent.evaluation import meets_objective
        action = Action("skill", servant=1, skill=2, target=3)
        objective = {"plan_includes_any": ["oc-f1-b1", "oc-f2-b1"]}
        self.assertTrue(meets_objective(action, objective, ["s1-2-t3", "s2-2-t3", "oc-f1-b1"]))
        self.assertFalse(meets_objective(action, objective, ["s1-2-t3", "attack"]))
        self.assertFalse(meets_objective(action, objective, None))


class PlanExclusionTests(unittest.TestCase):
    def test_plan_excludes_all_rejects_any_listed_action(self):
        from fgo_agent.evaluation import meets_objective
        objective = {"plan_excludes_all": ["s1-2-t3", "s1-3-t3"]}
        action = Action("skill", servant=1, skill=1, target=2)
        self.assertTrue(meets_objective(action, objective, ["s1-1-t2", "attack"]))
        self.assertFalse(meets_objective(action, objective, ["s1-1-t2", "s1-3-t3", "attack"]))
        self.assertFalse(meets_objective(action, objective, None))
