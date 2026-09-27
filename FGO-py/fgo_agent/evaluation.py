"""Independent snapshot comparisons; these do not simulate outcomes or win rates."""
import ast
from dataclasses import asdict
import json
from pathlib import Path
import time

from .controller import BattleController
from .legacy_policy import LegacyPolicy
from .models import Observation


def load_legacy_metadata(path=None):
    """Read the literal table without importing the upstream image/OCR runtime."""
    path = Path(path or Path(__file__).parents[1] / 'fgoMetadata.py')
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'servantData' for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError('No literal servantData found')


def load_cases(path, limit=20, trace=False):
    if not 1 <= limit <= 50:
        raise ValueError('Evaluation limit must be 1..50')
    path = Path(path)
    if not trace:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        if data.get('version') != 1 or not isinstance(data.get('cases'), list):
            raise ValueError('Unsupported evaluation suite')
        return data['cases'][:limit]
    observations, cases = {}, []
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        event = json.loads(line)
        payload = event.get('payload', {})
        if event.get('event') == 'observation':
            observations[payload['id']] = payload
        elif event.get('event') == 'decision' and payload.get('observation_id') in observations:
            cases.append({'name': payload['observation_id'], 'source': 'recorded_snapshot',
                          'observation': observations[payload['observation_id']]})
    # Cover both tactical and card screens across the battle instead of just its opening skills.
    if len(cases) > limit:
        indexes = sorted({round(i * (len(cases)-1) / max(1, limit-1)) for i in range(limit)})
        cases = [cases[i] for i in indexes]
    return cases


def meets_objective(action, objective, plan_ids=None):
    """Judge one decision. ``plan_includes_any`` checks the whole planned turn,
    for objectives about what a plan contains rather than what it does first."""
    if not objective:
        return None
    if 'any_of' in objective:
        return any(meets_objective(action, item, plan_ids) for item in objective['any_of'])
    if 'plan_includes_any' in objective:
        return any(identifier in (plan_ids or []) for identifier in objective['plan_includes_any'])
    if 'plan_excludes_all' in objective:
        return plan_ids is not None and not any(identifier in plan_ids
                                                for identifier in objective['plan_excludes_all'])
    data = asdict(action)
    for key, value in objective.get('fields', {}).items():
        if data.get(key) != value:
            return False
    if any(slot not in action.cards for slot in objective.get('cards_include', [])):
        return False
    if any(slot in action.cards for slot in objective.get('cards_exclude', [])):
        return False
    return True


def evaluate(cases, servant_data, planner_factory, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for case in cases:
        observation = Observation(**case['observation'])
        row = {'name': case['name'], 'source': case.get('source', 'synthetic'),
               'objective': case.get('objective'), 'objective_rationale': case.get('objective_rationale')}
        # Each policy receives this snapshot independently, with no future outcomes.
        for label, policy in [('baseline', LegacyPolicy(servant_data)), ('planner', planner_factory())]:
            start = time.monotonic()
            try:
                action = policy.next_action(observation)
                error = BattleController(None)._validate(action, observation)
                plan_ids = (policy.last_decision or {}).get('planned_action_ids') or []
                row[label] = {'action': asdict(action), 'legal': error is None, 'legality_error': error,
                              'objective_met': meets_objective(action, case.get('objective'), plan_ids),
                              'decision': policy.last_decision}
            except Exception as exc:
                row[label] = {'error': f'{type(exc).__name__}: {exc}', 'legal': False, 'objective_met': False}
            row[label]['elapsed_seconds'] = round(time.monotonic() - start, 3)
        rows.append(row)
        with (out / 'comparisons.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        print(f"{case['name']}: baseline legal={row['baseline']['legal']}, planner legal={row['planner']['legal']}", flush=True)
    objective_rows = [row for row in rows if row['objective']]
    summary = {'cases': len(rows), 'objective_cases': len(objective_rows),
               'independent_snapshots': True, 'outcomes_simulated': False,
               'metrics': {label: {'legal': sum(row[label]['legal'] for row in rows),
                                    'objective_met': sum(row[label]['objective_met'] is True for row in objective_rows),
                                    'errors': sum('error' in row[label] for row in rows)}
                           for label in ('baseline', 'planner')},
               'limits': 'Hand-authored objectives and independent recorded snapshots; not a win-rate estimate or proof of optimal choices.'}
    (out / 'evaluation.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    return summary
