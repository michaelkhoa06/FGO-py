"""Fixed target-choice checks, separate from live outcomes and existing suites."""
import copy
import json
from pathlib import Path

base = {'id':'target-threat', 'phase':'tactical', 'captured_at':'synthetic',
        'wave':1, 'turn':1, 'allies':[
            {'slot':i, 'id':sid, 'hp':12000, 'np':0, 'skills':[]} for i,sid in ((1,127),(2,314),(3,247))],
        'enemies':[{'slot':1,'hp':70000,'charge':[0,5]},
                   {'slot':2,'hp':1200,'charge':[5,5]},
                   {'slot':3,'hp':70000,'charge':[0,5]}],
        'signals':{'enemy_layout':'standard3','selected_enemy':1,
                   'enemy_target_verifiable':True,'stage_total':3}}
cases=[]
for name, change, objective in [
    ('focus-full-charge-threat',{}, {'kind':'enemy_target','target':2}),
    ('keep-current-threat',{'signals':{'selected_enemy':2}}, {'kind':'attack'}),
    ('unknown-highlight-no-target',{'signals':{'selected_enemy':None,'enemy_target_verifiable':False}}, {'kind':'attack'}),
    ('unknown-hp-no-target',{'unknown_hp':True}, {'kind':'attack'}),
]:
    obs=copy.deepcopy(base)
    obs['id']=name
    obs['signals'].update(change.get('signals',{}))
    if change.get('unknown_hp'):
        obs['enemies'][1]['hp']=None
    cases.append({'name':name,'source':'synthetic_targeting_regression',
                  'observation':obs,'objective':{'fields':objective},
                  'objective_rationale':'Prefer a visible full-charge threat when a legal target change is useful; do not fabricate missing state or change away from the threat.'})
Path(__file__).with_name('target-scenarios.json').write_text(json.dumps({'version':1,'cases':cases},indent=2),encoding='utf-8')
